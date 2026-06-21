# HomeHub Project Blueprint

**Version:** v1.0.2  
**Last Updated:** 2026-06-22  

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

### 3. Ekstensi API Eksternal
- **AI Agent Integration (Universal Router)**: `app/blueprints/ai_agent.py` - Menyediakan antarmuka "Tanpa Tatap Muka" bagi AI pihak ketiga via `POST /api/ai/execute`. Modul ini memungkinkan agen AI untuk mengatur status rumah dan membaca/mengubah Catatan Bersama (*Notes*), Daftar Tugas (*Chores*), dan Daftar Belanja (*Shopping List*).
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
