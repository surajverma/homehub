# HomeHub Project Blueprint

**Version:** v1.0.7  
**Last Updated:** 2026-07-01

Dokumen ini memetakan arsitektur dan modul utama dari proyek HomeHub, membantu *developer* memahami struktur fitur secara keseluruhan.

## Arsitektur Sistem
HomeHub dibangun di atas *stack* teknologi berikut:
- **Backend**: Python (Flask), menggunakan `Flask-SQLAlchemy` untuk interaksi *database*.
- **Database**: SQLite (tersimpan di `data/app.db`).
- **Frontend**: Vanilla JavaScript, Tailwind CSS (dibangun menggunakan `npm run build:css`), HTML + Jinja2 Templates. Baru saja diperbarui dengan refaktor UI/UX berstandar tinggi (aksesibilitas/A11y, touch targets 44px, transisi GPU-friendly, reduced-motion, standarisasi ikon Font Awesome).
- **Penyebaran (Deployment)**: Docker / Docker Compose & Github Actions (GHCR).

## Feature Modules

### 1. Inti (Core)
- **Config Loader**: `app/config.py` - Memuat konfigurasi utama dari `config.yml`. Mengatur fitur-fitur yang dinyalakan/dimatikan serta `family_members`.
- **Database Models**: `app/models.py` - Menyimpan representasi tabel (*Schema*) seperti Catatan, Chores, Belanja, Pengeluaran, dll.
- **Main Dashboard**: `app/blueprints/dashboard.py` - Pusat kendali web yang merangkum *Widget* seperti kalender, siapa di rumah, dan status personal.

### 2. Modul Fungsionalitas Keluarga
- **Shopping List**: Terletak di `app/blueprints/shopping.py`. Mirip dengan *Chores*, tapi khusus melacak daftar belanjaan beserta *tags* kategori dan histori belanja.
- **Quick Links**: Terletak di `app/blueprints/quick_links.py`. Fitur manajemen *bookmark* interaktif dengan dukungan *drag-and-drop* (menggunakan Sortable.js), ikon SVG CDN, dan Auto-favicon. Tautan dan Kategori dapat diatur urutannya secara fleksibel (*order_index* tersimpan di SQLite), lalu ditampilkan berjejer di *dashboard* utama (sebagai *dashboard* mini ala Heimdall/Homarr).
- **Expense Tracker**: Terletak di `app/blueprints/expenses.py`. Melacak pengeluaran dengan filter bulanan/tahunan, serta sistem penagihan (Belum Bayar/Lunas) untuk tagihan berulang rutin. Memiliki halaman manajemen dedikasi untuk aturan *recurring* di `/expenses/recurring` dengan *Edit Strategy* pengamanan histori pembayaran.
- **Shared Notes & Cloud**: Mengelola direktori penyimpanan file bersama dan catatan tempel.
- **Kalender Reminders**: Mengelola pengingat jadwal satu kali jalan maupun jadwal rutin.
- **Media Downloader & PDFs**: Terletak di `app/blueprints/media_pdfs.py`. Utilitas pengunduhan video/audio menggunakan `yt-dlp` dan konverter PDF. Terintegrasi erat dengan PWA (Progressive Web App) melalui dukungan **Web Share Target API**, memungkinkan pengguna di perangkat seluler untuk melempar tautan unduhan langsung ke aplikasi HomeHub. Dilengkapi **file existence validation** — backend mengecek keberadaan file sebelum serve, API status mengembalikan `file_exists` flag, dan antarmuka menampilkan status "Not available" jika file hilang (dihapus manual atau folder dipindah), mencegah broken links dan 404 yang membingungkan.
   - **PWA Web Share Target** — Fitur andalan yang memungkinkan pengguna Android membagikan tautan (misalnya dari YouTube) langsung ke Media Downloader via share sheet OS. Didukung oleh manifest `share_target` dan Service Worker fetch handler khusus untuk rute `/media/share`.

### 2a. PWA Configuration (Progressive Web App)
PWA HomeHub dikonfigurasi melalui dua endpoint dinamis di `app/blueprints/__init__.py`:
- **`/manifest.webmanifest`** — Manifest JSON dinamis yang mencakup: `name`, `short_name`, `icons` (192x192 + 512x512 dengan `purpose: "any maskable"`), `display: standalone`, `display_override`, `share_target` untuk Web Share Target API, `categories`, `description`, dan `launch_handler`.
- **`/sw.js`** — Service Worker dengan strategi offline-first: network-first untuk navigasi, stale-while-revalidate untuk aset statis, bypass cache untuk `/api/*` dan `/media/share`. Ikon PWA di-precache saat install.

### Syarat Infra untuk Share Target
1. **HTTPS** — Web Share Target API hanya berfungsi di secure context. Server harus diakses via HTTPS.
2. **Ikon maskable** — Android 12+ memerlukan ikon dengan `purpose: "maskable"` untuk adaptive icon.
3. **Instalasi penuh** — PWA harus di-install via prompt Chrome (bukan sekedar "Add to Home screen").

### Panduan HTTPS
Akses HTTPS bisa diatur via:
- **Caddy** (auto SSL): Tambah service Caddy ke compose.yml dengan Caddyfile `homehub.domain.com { reverse_proxy homehub:5000 }`.
- **Nginx + certbot**: Reverse proxy dengan SSL termination.
- **Tailscale Funnel**: HTTPS cert otomatis via `*.ts.net` — install Tailscale di server + Android, akses via `https://<machine>.ts.net:5000`.

### 3. Ekstensi API Eksternal
- **AI Agent Integration (Universal Router)**: `app/blueprints/ai_agent.py` - Menyediakan antarmuka "Tanpa Tatap Muka" bagi AI pihak ketiga via `POST /api/ai/execute` dan dokumentasi skema via `GET /api/ai/schema`. Modul ini memungkinkan agen AI untuk mengatur status rumah dan membaca/mengubah Catatan Bersama (*Notes*), Daftar Tugas (*Chores*), Daftar Belanja (*Shopping List*), Tautan Cepat (*Quick Links*), Pengaturan (`config.yml`), serta modul Keuangan (*Expense Tracker*) dengan dukungan unggahan bukti struk (Base64).
- **RESTful Config API**: `app/blueprints/config_api.py` - Memungkinkan sistem eksternal untuk mengubah preferensi bawaan aplikasi dan mengelola akun di `config.yml` secara programatis tanpa merusak komentar struktur file.
- **Keamanan**: Seluruh rute API ekstensi dijaga ketat menggunakan mekanisme `Authorization: Bearer <ai_agent_token>`.

## Struktur Direktori
```text
homehub/
├── app/
│   ├── blueprints/       # Folder Controller setiap fitur
│   ├── __init__.py       # Factory pattern untuk Setup Flask
│   ├── config.py         # YAML Parser
│   └── models.py         # SQLAlchemy Schema
├── data/                 # Penyimpanan SQLite Database lokal
├── static/               # Assets frontend (CSS/JS)
├── templates/            # Tampilan antarmuka HTML
├── CHANGELOG.md          # Log rekam jejak fitur
├── BLUEPRINT.md          # Dokumen ini
└── config.yml            # Pusat pengaturan aplikasi
```
