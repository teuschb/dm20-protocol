// Minimal service worker: lets the page show notifications (required on
// Android Chrome), receives Web Push events so a notification can reach the
// player even with every tab closed, and focuses the game tab when one is
// tapped. No caching.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', function (e) {
    var data = {};
    try { data = e.data ? e.data.json() : {}; } catch (err) { /* ignore malformed payload */ }
    var title = data.title || 'The DM';
    var body = data.body || '';
    e.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
            // If a tab is already open and visible, the live WebSocket path
            // (notifyNew() in app.js) already surfaced this — don't double up.
            var visible = list.some(function (c) { return c.visibilityState === 'visible'; });
            if (visible) return;
            return self.registration.showNotification(title, {
                body: body,
                tag: data.tag || 'dm-reply',
                renotify: true,
            });
        })
    );
});

self.addEventListener('notificationclick', function (e) {
    e.notification.close();
    e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
        for (var i = 0; i < list.length; i++) {
            if (list[i].url.indexOf('/play') !== -1 && 'focus' in list[i]) return list[i].focus();
        }
    }));
});
