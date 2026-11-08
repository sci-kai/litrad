// litresearch service worker (version __VERSION__).
// Only static files and the offline page are cached. Pages with your data are
// never stored: navigations always go to the network.
"use strict";
const CACHE = "litresearch-v__VERSION__";
const OFFLINE = "/offline";
const PRECACHE = [
  OFFLINE,
  "/static/app.css",
  "/static/app.js",
  "/static/favicon.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-maskable-512.png",
  "/static/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE && key.startsWith("litresearch-")).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return; // never touch (or cache) POST and friends
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    // cache first; the cache name changes with every release
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone();
              caches.open(CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
      )
    );
  } else if (request.mode === "navigate") {
    // network first; the offline page only when the server cannot be reached
    event.respondWith(
      fetch(request).then(
        (response) => (response.status === 502 || response.status === 503 || response.status === 504 ? caches.match(OFFLINE) : response),
        () => caches.match(OFFLINE)
      )
    );
  }
});
