// Network-first for the feed, cache-first for post images, so the app opens
// instantly and still shows the last posts when offline.
const CACHE = 'studio-v11';
const SHELL = ['./', 'index.html', 'stats.html', 'manifest.json', 'icons/apple-touch-icon.png', 'icons/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  if (url.pathname.includes('/posts/')) {
    e.respondWith(caches.match(e.request).then(hit => hit || fetch(e.request).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(e.request, copy));
      return res;
    })));
    return;
  }

  e.respondWith(fetch(e.request).then(res => {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(url.pathname, copy));
    if (url.pathname.endsWith('/feed.json')) cleanUp(res.clone());
    return res;
  }).catch(() => caches.match(url.pathname, { ignoreSearch: true })));
});

// Delete saved images of posts that are no longer in the feed, so the app
// doesn't keep filling the phone's storage.
async function cleanUp(feedResponse) {
  try {
    const posts = await feedResponse.json();
    try { const r = await fetch('ready.json?t=' + Date.now()); if (r.ok) posts.push(...await r.json()); } catch {}
    const keep = new Set();
    for (const p of posts) {
      [p.image, ...(p.options || []), ...(p.slides || [])].forEach(src => keep.add(src));
    }
    const cache = await caches.open(CACHE);
    for (const req of await cache.keys()) {
      const path = new URL(req.url).pathname;
      const i = path.indexOf('/posts/');
      if (i !== -1 && !keep.has(path.slice(i + 1))) await cache.delete(req);
    }
  } catch (err) { /* offline or bad JSON: try again next time */ }
}
