const CACHE='familygraph-shell-v3';
const SHELL=['/static/manifest.json','/static/app-icon.svg'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('message',event=>{if(event.data?.type==='SKIP_WAITING')self.skipWaiting();});
self.addEventListener('fetch',event=>{
  if(event.request.method!=='GET') return;
  const url=new URL(event.request.url);
  if(url.pathname.startsWith('/static/')) event.respondWith(caches.match(event.request).then(hit=>hit||fetch(event.request)));
});

// ── Web Push: نبض هفتگی روابط ──
self.addEventListener('push',event=>{
  let data={};
  try{ data=event.data ? event.data.json() : {}; }catch(e){ data={body:event.data&&event.data.text()}; }
  const title=data.title||'FamilyGraph';
  event.waitUntil(self.registration.showNotification(title,{
    body:data.body||'',
    icon:'/static/app-icon.svg',
    badge:'/static/app-icon.svg',
    tag:data.tag||'familygraph',
    data:{url:data.url||'/'},
    dir:'rtl',
    lang:'fa'
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=(event.notification.data&&event.notification.data.url)||'/';
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(list=>{
    for(const c of list){ if('focus' in c){ c.navigate(target); return c.focus(); } }
    return clients.openWindow(target);
  }));
});
