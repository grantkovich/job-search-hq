// Service worker: shows push notifications and handles their buttons, and shows a plain
// "you're offline" page when a page can't load. The only things cached are that page and its
// stylesheet and icon, which hold no personal data; private pages and API answers are never stored.
const CACHE = "jobhq-offline-v1";
const OFFLINE = "/static/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll([OFFLINE, "/static/app.css", "/static/icon.svg"])).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Only page loads are handled, and only when the network fails. Everything else goes straight to the network.
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE).then((r) => r || Response.error())));
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = { title: "Job Search HQ", body: event.data ? event.data.text() : "" }; }
  const actions = (data.actions || []).slice(0, 2).map((a) => ({ action: a.action + "|" + (a.token || ""), title: a.title }));
  event.waitUntil(self.registration.showNotification(data.title || "Job Search HQ", {
    body: data.body || "", tag: data.tag || undefined, icon: "/static/icon-192.png", badge: "/static/icon-192.png",
    data: { url: typeof data.url === "string" && data.url.startsWith("/") ? data.url : "/" }, actions,
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const [action, token] = (event.action || "").split("|");
  if (action && token) {
    event.waitUntil(fetch("/push/action/" + encodeURIComponent(token) + "/" + encodeURIComponent(action), { method: "POST", credentials: "same-origin" }));
    return;
  }
  const url = new URL(event.notification.data && event.notification.data.url || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    for (const w of wins) { if (w.url.startsWith(self.location.origin)) { w.focus(); return w.navigate(url); } }
    return self.clients.openWindow(url);
  }));
});
