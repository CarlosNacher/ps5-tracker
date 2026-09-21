# PS5 Watch

Rastreador de precios de PS5 Slim Digital, PS5 Slim con lector, PS5 Pro y mando DualSense en seis
tiendas españolas. GitHub Actions consulta los precios dos veces al día, los guarda en
`data/prices.json` y avisa por Telegram cuando algo se mueve. La web lee ese histórico y dibuja las
gráficas; se instala en el iPhone desde Compartir → Añadir a pantalla de inicio.

No hay servidor, ni base de datos, ni coste. El histórico es el propio repositorio.

---

## Montaje (unos 15 minutos)

### 1. Sube el repo

Crea un repositorio **público** llamado `ps5-tracker` (público para que GitHub Pages sea gratis) y
sube estos archivos. Desde el móvil también vale: en la web de GitHub, *Add file → Upload files*.

### 2. Crea el bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather) → `/newbot` → nombre y usuario.
   Te devuelve un token tipo `8123456789:AAF...`.
2. Escríbele algo a tu bot recién creado (si no, no puede contestarte).
3. Consigue tu chat id: habla con [@userinfobot](https://t.me/userinfobot) o abre
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y busca `"chat":{"id":...}`.

### 3. Guarda los secretos

En el repo: *Settings → Secrets and variables → Actions → New repository secret*.

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token del BotFather |
| `TELEGRAM_CHAT_ID` | tu chat id (un número, puede ser negativo si es un grupo) |

### 4. Enciende Pages

*Settings → Pages → Source: Deploy from a branch → main / (root)*.
Tu app queda en `https://TU-USUARIO.github.io/ps5-tracker/`.

Edita `config.json` y pon tu repo en el campo `"repo"` (`"TU-USUARIO/ps5-tracker"`); se usa para el
enlace de las notificaciones y el botón de editar objetivos.

### 5. Arregla las URLs (importante)

Las URLs de `config.json` son **plantillas de ejemplo**: los identificadores de producto de
MediaMarkt, Fnac, GAME y El Corte Inglés cambian y no valen copiados a ciegas. Abre cada ficha de
producto en el móvil, copia la URL real y pégala. Para comprobarlo de golpe:

```bash
pip install -r requirements.txt
python scripts/track.py --check-urls
```

Cada línea con ✗ es una URL que hay que sustituir. Puedes borrar tiendas que no te interesen
simplemente quitándolas del bloque `urls` de cada producto.

### 6. Primer disparo

*Actions → Track prices → Run workflow*. Al terminar verás un commit nuevo con los precios y la app
ya pintará algo. A partir de ahí corre sola a las 07:10 y 19:10 (hora de Madrid).

### 7. Instálala en el iPhone

Abre la URL en Safari (tiene que ser Safari, no Chrome) → botón Compartir → Añadir a pantalla de
inicio. Se abre a pantalla completa, sin barra de navegador, y guarda el armazón en caché para abrir
al instante.

---

## Cómo funcionan los avisos

Todo se configura en el bloque `alerts` de `config.json`:

- **Bajada** — el precio de hoy cae un 10 % o más frente al último registro o frente a la mediana de
  los últimos 30 días. Se usan las dos referencias porque la primera pilla las ofertas flash y la
  segunda las bajadas sostenidas.
- **Subida** — lo mismo al revés, para que sepas si te has quedado sin la ventana.
- **Mínimo histórico** — cualquier precio por debajo de todo lo visto antes.
- **Objetivo** — cuando toca el `target` que le pongas a cada producto.

Para que no te llegue el mismo aviso cada mañana hay un `cooldown_days` de 3 días: solo repite antes
si el precio se mueve otro 2 %. Cambia `drop_pct`, `rise_pct` o los objetivos editando `config.json`
(la app tiene un enlace directo a esa edición en el pie).

## Qué se va a romper y qué hacer

- **Amazon devuelve 403.** Es lo normal desde las IPs de GitHub Actions. El script lo reintenta y, si
  no hay manera, lo marca como fallo y sigue con las demás. Para Amazon concretamente compensa más
  usar una alerta de Keepa en paralelo.
- **Una tienda cambia el HTML.** El script intenta cuatro métodos por orden (JSON-LD, meta etiquetas,
  selectores CSS, y de último recurso el euro más bajo de la página). Si aun así falla seis veces
  seguidas, te llega un aviso de mantenimiento por Telegram. Se arregla actualizando el `css` de esa
  tienda en `config.json`.
- **Céntimos raros.** Algunas fichas parten el precio en dos elementos (649 · 99). Si ves precios
  redondeados, añade el selector bueno al principio de la lista `css` de esa tienda.
- **El cron llega tarde.** GitHub retrasa los workflows programados en horas punta, a veces 20-30
  minutos. Para nuestro caso da igual.
- **La app no se actualiza.** Cierra y reabre; el service worker pide siempre los precios a la red,
  pero iOS es perezoso al despertar la app.

## Probar en local

```bash
python scripts/track.py --dry-run     # consulta y muestra, sin escribir ni notificar
python -m http.server 8000            # y abre http://localhost:8000
```

Sin datos todavía, la app ofrece un botón de datos de ejemplo para ver la interfaz.

## Archivos

```
index.html              la app: lista, gráficas, objetivos
sw.js                   caché del armazón para abrir offline
manifest.webmanifest    nombre, icono y modo pantalla completa
config.json             productos, tiendas, URLs, objetivos y reglas de aviso
scripts/track.py        consulta precios, guarda histórico y notifica
data/prices.json        el histórico (lo escribe la acción)
data/alerts_state.json  memoria antispam de los avisos
.github/workflows/      el cron
```
