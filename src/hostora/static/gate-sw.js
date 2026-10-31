const CACHE_NAME = "hostora-gate-v1";
const PRECACHE_URLS = [
  "/static/css/app.css",
  "/static/vendor/noble-ed25519.js",
  "/static/vendor/jsQR.js",
  "/static/js/gate/app.js",
  "/static/js/gate/db.js",
  "/static/js/gate/verify.js",
  "/static/js/gate/sync.js",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;

  // Manifest is network-first
  if (url.pathname.includes("/manifest")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Static and gate shell assets are cache-first
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        if (resp.ok && (url.pathname.startsWith("/static/") || url.pathname.startsWith("/gate/"))) {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return resp;
      });
    })
  );
});
