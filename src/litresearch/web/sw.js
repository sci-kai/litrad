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
// answers that mean "the server is down", not "your request failed"
const SERVER_DOWN = new Set([502, 503, 504]);

async function prepare() {
  const store = await caches.open(CACHE);
  await store.addAll(PRECACHE);
  await self.skipWaiting();
}

async function forgetOldReleases() {
  for (const name of await caches.keys()) {
    if (name.startsWith("litresearch-") && name !== CACHE) await caches.delete(name);
  }
  await self.clients.claim();
}

// static files: the cache name changes with every release, so a stored copy is current
async function staticFile(request) {
  const stored = await caches.match(request);
  if (stored) return stored;
  const response = await fetch(request);
  if (response.ok) {
    const store = await caches.open(CACHE);
    await store.put(request, response.clone());
  }
  return response;
}

// pages: always from the server; the offline page only when it cannot answer
async function page(request) {
  let response;
  try {
    response = await fetch(request);
  } catch {
    return caches.match(OFFLINE);
  }
  return SERVER_DOWN.has(response.status) ? caches.match(OFFLINE) : response;
}

function route(request) {
  if (request.method !== "GET") return null; // never touch (or cache) POST and friends
  const { origin, pathname } = new URL(request.url);
  if (origin !== self.location.origin) return null;
  if (pathname.startsWith("/static/")) return staticFile;
  return request.mode === "navigate" ? page : null;
}

self.addEventListener("install", (event) => event.waitUntil(prepare()));
self.addEventListener("activate", (event) => event.waitUntil(forgetOldReleases()));
self.addEventListener("fetch", (event) => {
  const handler = route(event.request);
  if (handler) event.respondWith(handler(event.request));
});
