const CACHE='familygraph-shell-v1';
const SHELL=['/static/manifest.json','/static/app-icon.svg'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET') return;
  const url=new URL(event.request.url);
  if(url.pathname.startsWith('/static/')) event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request)));
});
