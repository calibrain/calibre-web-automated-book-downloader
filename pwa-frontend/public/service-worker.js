// pwa-frontend/public/service-worker.js
// This is a basic service worker for PWA caching strategy.
// For a production app, consider using workbox-webpack-plugin or similar.

const CACHE_NAME = 'calibre-downloader-pwa-cache-v1';
const urlsToCache = [
  '/',
  '/index.html',
  '/src/index.js',
  '/src/App.js',
  '/src/index.css',
  '/manifest.json',
  '/icon-192x192.png', // You would need to create these icon files
  '/icon-512x512.png',
  'https://cdn.tailwindcss.com', // Cache Tailwind CDN if used directly
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Opened cache');
        return cache.addAll(urlsToCache);
      })
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request)
      .then((response) => {
        if (response) {
          return response;
        }
        return fetch(event.request);
      })
  );
});

self.addEventListener('activate', (event) => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    Promise.all(
      Object.keys(caches).map(cacheName => {
        if (cacheWhitelist.indexOf(cacheName) === -1) {
          return caches.delete(cacheName);
        }
        return null; // Return null for caches that should not be deleted
      })
    )
  );
});
