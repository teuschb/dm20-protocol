// Minimal service worker: lets the page show notifications (required on Android
// Chrome) and focuses the game tab when one is tapped. No caching, no push.
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('notificationclick', function (e) {
    e.notification.close();
    e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (list) {
        for (var i = 0; i < list.length; i++) {
            if (list[i].url.indexOf('/play') !== -1 && 'focus' in list[i]) return list[i].focus();
        }
    }));
});
