/* Service worker solo para avisos push. No guarda nada en caché ni intercepta peticiones:
   las páginas siguen con Cache-Control no-store. */
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener('push', function (e) {
  var d = {};
  try { d = e.data ? e.data.json() : {}; } catch (x) { d = { body: e.data ? e.data.text() : '' }; }
  var url = (typeof d.url === 'string' && d.url.charAt(0) === '/') ? d.url : '/portal';
  e.waitUntil(self.registration.showNotification(d.title || 'For Rent Online', {
    body: d.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/favicon-48.png',
    tag: d.tag || 'for-rent',
    data: { url: url }
  }));
});
self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || '/portal';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (cs) {
    for (var i = 0; i < cs.length; i++) {
      var c = cs[i];
      if (c.url.indexOf(self.location.origin) === 0 && 'navigate' in c) {
        return c.navigate(url).then(function (w) { return (w || c).focus(); });
      }
    }
    return self.clients.openWindow(url);
  }));
});
