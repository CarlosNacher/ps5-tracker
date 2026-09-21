// Cachea el armazón para que la app abra al instante; los precios siempre se piden a la red.
const CACHE = "ps5watch-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;

  // Datos y config: red primero, caché como red de seguridad si estás sin cobertura.
  if (url.pathname.endsWith("prices.json") || url.pathname.endsWith("config.json")) {
    e.respondWith(
      fetch(e.request).then(r => {
        caches.open(CACHE).then(c => c.put(e.request, r.clone()));
        return r;
      }).catch(() => caches.match(e.request, {ignoreSearch: true}))
    );
    return;
  }

  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
