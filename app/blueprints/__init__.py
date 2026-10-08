from flask import Blueprint, current_app, Response
from flask_babel import gettext as _
from markupsafe import escape
import json
import os
import subprocess

from ..i18n import active_language

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


_sw_version_cache = []


def _sw_cache_version():
    """SW cache version: ENV first, then git tag, then constant. Computed once per process."""
    if _sw_version_cache:
        return _sw_version_cache[0]
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
    _sw_version_cache.append(version or DEFAULT_SW_CACHE_VERSION)
    return _sw_version_cache[0]


@main_bp.route('/sw.js')
def service_worker():
    try:
        # Offline-first SW with runtime caching and navigation fallback
        version = _sw_cache_version()
        # The language is part of the cache name so switching it drops pages cached in the old one
        language = active_language()
        offline_html = (
            f"<!DOCTYPE html><title>{escape(_('Offline'))}</title><h1>{escape(_('You are offline'))}</h1>"
            f"<p>{escape(_('This page is not available offline.'))}</p>"
        )

        sw_js = r"""
        const CACHE_NAME = 'homehub-v__VERSION__-__LANGUAGE__';
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

          // Bypass caching for dynamic API endpoints to avoid stale data
          if (url.pathname.startsWith('/api/')) {
            event.respondWith(fetch(req));
            return;
          }

          // Navigation requests: try network, fallback to cache, then to '/'
          if (req.mode === 'navigate') {
            event.respondWith(
              fetch(req)
                .catch(() => caches.match(req))
                .then(res => res || caches.match('/'))
                .then(res => res || new Response(
                  __OFFLINE_HTML__,
                  { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
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
        """.replace('__VERSION__', version).replace('__LANGUAGE__', language)
        sw_js = sw_js.replace('__OFFLINE_HTML__', json.dumps(offline_html))

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
