from flask import Blueprint, current_app, Response
import json
import os
import subprocess

# Single app-wide blueprint to preserve all existing URL paths and endpoint names
main_bp = Blueprint('main', __name__)

# Default SW cache version; can be overridden by env SW_CACHE_VERSION or git tag
DEFAULT_SW_CACHE_VERSION = "1"


@main_bp.route('/manifest.webmanifest')
def manifest_webmanifest():
    cfg = current_app.config.get('HOMEHUB_CONFIG', {})
    theme = cfg.get('theme', {})
    name = cfg.get('instance_name', 'HomeHub')
    short_name = (name[:12] + '…') if len(name) > 13 else name
    manifest = {
        "name": name,
        "short_name": short_name,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": theme.get('background_color', '#ffffff'),
        "theme_color": theme.get('primary_color', '#2563eb'),
        "icons": [
            {"src": "/static/icons/icon-192.png", "type": "image/png", "sizes": "192x192", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "type": "image/png", "sizes": "512x512", "purpose": "any"},
            {"src": "/static/icons/homehub.svg", "type": "image/svg+xml", "sizes": "any", "purpose": "any"}
        ]
    }
    return Response(json.dumps(manifest), mimetype='application/manifest+json')


@main_bp.route('/sw.js')
def service_worker():
    try:
        # Offline-first SW with runtime caching and navigation fallback
        # Determine cache version: ENV first, then git tag, then constant
        version = os.environ.get('SW_CACHE_VERSION')
        if not version:
            try:
                repo_root = os.path.abspath(os.path.join(current_app.root_path, '..'))
                res = subprocess.run(
                    ['git', 'describe', '--tags', '--always'],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=1.5
                )
                if res.returncode == 0:
                    version = (res.stdout or '').strip()
                    if version.startswith('v'):
                        version = version[1:]
            except Exception:
                version = None
        if not version:
            version = DEFAULT_SW_CACHE_VERSION

        sw_js = r"""
        const CACHE_NAME = 'homehub-v__VERSION__';
        const PRECACHE = [
          '/',
          '/static/output.css',
          '/static/js/reminders_api.js'
        ];

        self.addEventListener('install', (event) => {
          event.waitUntil(
            caches.open(CACHE_NAME).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
          );
        });

        self.addEventListener('activate', (event) => {
          event.waitUntil(
            caches.keys().then(keys => Promise.all(keys.map(k => { if(k !== CACHE_NAME) return caches.delete(k); }))).then(() => self.clients.claim())
          );
        });

        self.addEventListener('fetch', (event) => {
          const req = event.request;
          const url = new URL(req.url);
          // Only handle GET
          if (req.method !== 'GET') return;

          // Navigation requests: try network, fallback to cache, then to '/'
          if (req.mode === 'navigate') {
            event.respondWith(
              fetch(req)
                .catch(() => caches.match(req))
                .then(res => res || caches.match('/'))
                .then(res => res || new Response(
                  '<!DOCTYPE html><title>Offline</title><h1>You are offline</h1><p>This page is not available offline.</p>',
                  { status: 503, headers: { 'Content-Type': 'text/html' } }
                ))
            );
            return;
          }

          // Same-origin: stale-while-revalidate
          if (url.origin === location.origin) {
            event.respondWith(
              caches.match(req).then(cached => {
                const fetchPromise = fetch(req).then(networkRes => {
                  const clone = networkRes.clone();
                  caches.open(CACHE_NAME).then(cache => cache.put(req, clone));
                  return networkRes;
                }).catch(() => cached);
                return cached || fetchPromise;
              })
            );
          }
        });
        """.replace('__VERSION__', version)

        resp = Response(sw_js, mimetype='application/javascript')
        # Ensure browsers always revalidate sw.js
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
    except Exception:
        current_app.logger.exception('Failed to generate service worker script')
        fallback = (
            "self.addEventListener('install',()=>self.skipWaiting());"
            "self.addEventListener('activate',e=>self.clients.claim());"
        )
        resp = Response(fallback, mimetype='application/javascript')
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return resp
