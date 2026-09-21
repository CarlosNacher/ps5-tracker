#!/usr/bin/env python3
"""
Comprueba el precio de cada producto en cada tienda, lo guarda en data/prices.json
y avisa por Telegram cuando algo se mueve de verdad.

Uso:
    python scripts/track.py              # comprueba, guarda y notifica
    python scripts/track.py --dry-run    # comprueba y muestra, sin guardar ni notificar
    python scripts/track.py --check-urls # solo dice si cada URL responde y si saca precio
"""

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:
    # Imita la huella TLS de Chrome. Sin esto, Cloudflare y Akamai
    # devuelven 403 antes incluso de mirar las cabeceras.
    from curl_cffi import requests as impersonator
    IMPERSONA = "chrome124"
except ImportError:
    impersonator = None
    IMPERSONA = None

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
PRICES = ROOT / "data" / "prices.json"
STATE = ROOT / "data" / "alerts_state.json"
MADRID = ZoneInfo("Europe/Madrid")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Connection": "keep-alive",
}

PRICE_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,(\d{2}))?\s*(?:€|EUR)")


# ---------------------------------------------------------------- utilidades


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"! {path.name} ilegible, arranco de cero", file=sys.stderr)
    return default


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def to_float(raw):
    """'1.299,00 €' -> 1299.0 ; '649.99' -> 649.99 ; '55' -> 55.0"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    txt = str(raw).strip().replace("\xa0", " ")
    m = PRICE_RE.search(txt)
    if m:
        entero = re.sub(r"[.\s]", "", m.group(1))
        decimal = m.group(2) or "00"
        try:
            return round(float(f"{entero}.{decimal}"), 2)
        except ValueError:
            return None
    txt = re.sub(r"[^\d,.]", "", txt)
    if not txt:
        return None
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    elif txt.count(".") >= 1:
        # '1.099' es mil noventa y nueve; '549.99' son céntimos.
        cola = txt.rsplit(".", 1)[-1]
        if txt.count(".") > 1 or len(cola) == 3:
            txt = txt.replace(".", "")
    try:
        val = round(float(txt), 2)
    except ValueError:
        return None
    return val


def sane(price):
    return price is not None and 20 <= price <= 2000


def money(v):
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------------------------------------------------------------- extracción


def from_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in blob if isinstance(blob, list) else [blob]:
            if not isinstance(node, dict):
                continue
            stack = [node] + (
                node.get("@graph") if isinstance(node.get("@graph"), list) else []
            )
            for item in stack:
                if not isinstance(item, dict):
                    continue
                offers = item.get("offers")
                for offer in offers if isinstance(offers, list) else [offers]:
                    if isinstance(offer, dict):
                        val = to_float(
                            offer.get("price") or offer.get("lowPrice")
                        )
                        if sane(val):
                            return val
    return None


def from_meta(soup):
    selectors = [
        ("meta", {"property": "product:price:amount"}),
        ("meta", {"itemprop": "price"}),
        ("meta", {"name": "twitter:data1"}),
    ]
    for tag, attrs in selectors:
        el = soup.find(tag, attrs=attrs)
        if el:
            val = to_float(el.get("content") or el.get_text())
            if sane(val):
                return val
    el = soup.find(attrs={"itemprop": "price"})
    if el:
        val = to_float(el.get("content") or el.get_text())
        if sane(val):
            return val
    return None


def from_css(soup, selectors):
    for sel in selectors or []:
        for el in soup.select(sel):
            val = to_float(el.get("content") or el.get_text())
            if sane(val):
                return val
    return None


def from_regex(html):
    candidates = [to_float(m.group(0)) for m in PRICE_RE.finditer(html)]
    candidates = [c for c in candidates if sane(c)]
    return min(candidates) if candidates else None


def descargar(url, timeout=30):
    """Baja la página imitando a Chrome. Devuelve (html, status, error)."""
    dominio = re.sub(r"^https?://([^/]+).*", r"\1", url)
    cabeceras = dict(HEADERS)
    # Llegar "desde Google" levanta menos sospechas que entrar a pelo.
    cabeceras["Referer"] = "https://www.google.com/"
    cabeceras["Sec-Fetch-Site"] = "cross-site"

    if impersonator is not None:
        try:
            with impersonator.Session(impersonate=IMPERSONA) as s:
                # Primero la portada, para recoger las cookies que planta el WAF.
                try:
                    s.get(f"https://{dominio}/", headers=cabeceras, timeout=timeout)
                    time.sleep(1 + random.random())
                except Exception:
                    pass
                r = s.get(url, headers=cabeceras, timeout=timeout)
                return r.text, r.status_code, None
        except Exception as e:
            return None, None, f"{type(e).__name__} (curl_cffi)"

    try:
        r = requests.get(url, headers=cabeceras, timeout=timeout)
        return r.text, r.status_code, None
    except requests.RequestException as e:
        return None, None, type(e).__name__


def scrape(url, provider):
    """Devuelve (precio, método, error)."""
    last_err = None
    for intento in range(3):
        html, status, error = descargar(url)
        if error:
            last_err = error
            time.sleep(3 + intento * 5)
            continue
        if status in (403, 429, 503):
            last_err = f"HTTP {status} (bloqueo)"
            time.sleep(5 + intento * 8 + random.random() * 4)
            continue
        if status >= 400:
            return None, None, f"HTTP {status}"

        soup = BeautifulSoup(html, "lxml")
        for estrategia in provider.get("strategies", ["jsonld", "meta", "regex"]):
            if estrategia == "jsonld":
                val = from_jsonld(soup)
            elif estrategia == "meta":
                val = from_meta(soup)
            elif estrategia == "css":
                val = from_css(soup, provider.get("css"))
            elif estrategia == "regex":
                val = from_regex(html)
            else:
                val = None
            if sane(val):
                return val, estrategia, None
        return None, None, "sin precio en la página"
    return None, None, last_err or "fallo de red"


# ---------------------------------------------------------------- histórico


def day_min(entries, dias=None):
    """Serie diaria: para cada día, el precio más bajo visto ese día."""
    corte = None
    if dias:
        corte = (datetime.now(MADRID) - timedelta(days=dias)).strftime("%Y-%m-%d")
    por_dia = {}
    for stamp, price in entries:
        d = stamp[:10]
        if corte and d < corte:
            continue
        por_dia[d] = min(por_dia.get(d, price), price)
    return [por_dia[d] for d in sorted(por_dia)]


# ---------------------------------------------------------------- alertas


def evaluar(cfg, prod, prov_id, prov_name, precio, entries, state):
    """Lista de alertas (tipo, texto) para este producto/tienda."""
    reglas = cfg["alerts"]
    avisos = []
    previos = [p for _, p in entries]
    ventana = day_min(entries, reglas["median_window_days"])
    anterior = previos[-1] if previos else None
    mediana = statistics.median(ventana) if ventana else None
    minimo = min(previos) if previos else None
    objetivo = prod.get("target")
    etiqueta = f"{prod['name']} · {prov_name}"

    def gate(tipo, precio_actual):
        """Evita repetir el mismo aviso a diario si el precio no se mueve más."""
        key = f"{prod['id']}|{prov_id}|{tipo}"
        prev = state.get(key)
        if prev:
            dias = (
                datetime.now(MADRID).date()
                - datetime.fromisoformat(prev["date"]).date()
            ).days
            movimiento = abs(precio_actual - prev["price"]) / prev["price"] * 100
            if dias < reglas["cooldown_days"] and movimiento < reglas["renotify_move_pct"]:
                return False
        state[key] = {
            "date": datetime.now(MADRID).date().isoformat(),
            "price": precio_actual,
        }
        return True

    if reglas.get("notify_target") and objetivo and precio <= objetivo:
        if gate("target", precio):
            avisos.append(
                (
                    "target",
                    f"🎯 *{etiqueta}*\n{money(precio)} — has puesto el objetivo en {money(objetivo)}.",
                )
            )

    if reglas.get("notify_all_time_low") and minimo and precio < minimo:
        if gate("low", precio):
            avisos.append(
                (
                    "low",
                    f"🟢 *{etiqueta}*\nMínimo histórico: {money(precio)} (antes {money(minimo)}).",
                )
            )

    for base, nombre in ((anterior, "ayer"), (mediana, f"la mediana de {reglas['median_window_days']} días")):
        if not base:
            continue
        delta = (precio - base) / base * 100
        if delta <= -reglas["drop_pct"]:
            if gate("drop", precio):
                avisos.append(
                    (
                        "drop",
                        f"📉 *{etiqueta}*\n{money(precio)} · {delta:+.1f}% frente a {nombre} ({money(base)}).",
                    )
                )
            break
        if delta >= reglas["rise_pct"]:
            if gate("rise", precio):
                avisos.append(
                    (
                        "rise",
                        f"📈 *{etiqueta}*\n{money(precio)} · {delta:+.1f}% frente a {nombre} ({money(base)}). Si lo querías, se acaba de encarecer.",
                    )
                )
            break

    return avisos


def telegram(texto, dry=False):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if dry or not token or not chat:
        print("\n--- Telegram (no enviado) ---\n" + texto)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": texto,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )
        if not r.ok:
            print(f"! Telegram respondió {r.status_code}: {r.text[:200]}", file=sys.stderr)
    except requests.RequestException as e:
        print(f"! Telegram falló: {e}", file=sys.stderr)


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-urls", action="store_true")
    args = ap.parse_args()

    cfg = load_json(CONFIG, None)
    if not cfg:
        sys.exit("config.json no encontrado")

    store = load_json(PRICES, {"updated": None, "series": {}, "status": {}})
    store.setdefault("series", {})
    store.setdefault("status", {})
    state = load_json(STATE, {})
    ahora = datetime.now(MADRID)
    stamp = ahora.strftime("%Y-%m-%d %H:%M")

    avisos, fallos, lineas = [], [], []

    for prod in cfg["products"]:
        store["series"].setdefault(prod["id"], {})
        for prov_id, url in prod.get("urls", {}).items():
            prov = cfg["providers"].get(prov_id, {})
            prov_name = prov.get("name", prov_id)
            precio, metodo, error = scrape(url, prov)
            skey = f"{prod['id']}|{prov_id}"
            estado = store["status"].get(skey, {"fails": 0})

            if precio is None:
                estado["fails"] = estado.get("fails", 0) + 1
                estado["last_error"] = error
                estado["last_try"] = stamp
                store["status"][skey] = estado
                fallos.append((f"{prod['name']} · {prov_name}", error, estado["fails"]))
                print(f"  ✗ {prod['id']:<12} {prov_id:<14} {error}")
                continue

            entries = store["series"][prod["id"]].get(prov_id, [])
            if not args.check_urls:
                avisos += evaluar(cfg, prod, prov_id, prov_name, precio, entries, state)
                entries.append([stamp, precio])
                store["series"][prod["id"]][prov_id] = entries[-1500:]
            estado.update({"fails": 0, "last_ok": stamp, "last_error": None, "method": metodo})
            store["status"][skey] = estado
            lineas.append(f"{prod['name']} · {prov_name}: {money(precio)}")
            print(f"  ✓ {prod['id']:<12} {prov_id:<14} {money(precio):>12}  ({metodo})")
            time.sleep(1.5 + random.random() * 2)

    if args.check_urls:
        print("\nRevisión terminada. Sustituye en config.json las URLs marcadas con ✗.")
        return

    store["updated"] = ahora.isoformat(timespec="minutes")

    if args.dry_run:
        print("\n[dry-run] no se guarda nada")
    else:
        save_json(PRICES, store)
        save_json(STATE, state)

    if avisos:
        cuerpo = "\n\n".join(t for _, t in avisos)
        pagina = f"\n\n[Ver gráficas](https://{cfg['repo'].split('/')[0]}.github.io/{cfg['repo'].split('/')[1]}/)"
        telegram(cuerpo + pagina, dry=args.dry_run)
    else:
        print("\nSin movimientos que merezcan aviso.")

    graves = [f for f in fallos if f[2] >= cfg["alerts"]["fail_streak_warning"]]
    if graves and not args.dry_run:
        texto = "🔧 *Tiendas que llevan varios días sin dar precio*\n" + "\n".join(
            f"· {n} ({e}, {c} intentos)" for n, e, c in graves
        )
        telegram(texto)

    print(f"\n{len(lineas)} precios registrados, {len(fallos)} fallos, {len(avisos)} avisos.")


if __name__ == "__main__":
    main()
