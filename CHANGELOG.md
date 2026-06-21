# Changelog

Semua perubahan yang signifikan pada proyek HomeHub ini akan dicatat di file ini.
Format penulisan berdasarkan [Keep a Changelog](https://keepachangelog.com/id/1.0.0/).

## [v1.0.1] - 2026-06-21

### Added
- **Delete Actions untuk AI Router**: AI sekarang bisa menghapus catatan, tugas, dan barang belanjaan lewat aksi `delete_note`, `delete_chore`, dan `delete_shopping_item`.
- **AI Universal Router Expansion**: Penambahan fungsi asisten AI untuk membaca/memanipulasi `config.yml` (Config API) serta memanipulasi Catatan (*Notes*), Tugas Rumah (*Chores*), dan Daftar Belanja (*Shopping List*) via `POST /api/ai/execute`.
- **AI Agent Universal Router**: API Endpoint (`/api/ai/execute`) tunggal untuk memungkinkan asisten AI pihak ketiga berinteraksi dengan seluruh *database* dan sistem *HomeHub* secara tersentralisasi.
- **Auto-Schema AI**: Endpoint (`/api/ai/schema`) yang mengembalikan format JSON OpenAI-compatible agar pengaturan *tool* AI lebih mudah.
- **Status Lunas/Belum Bayar (Expenses)**: Kolom `is_paid` pada pengeluaran dan penandaan visual (Lunas/Belum Bayar) di *Expense Tracker* dan kalender.
- **Pengaturan Horizon Tagihan Bulanan**: Tagihan berulang sekarang dicetak secara proaktif sampai akhir bulan saat ini untuk memudahkan perencanaan keuangan.

### Changed
- **Bug Fix (AI Tags Array)**: Memperbaiki masalah di mana pengiriman `tags` berupa *JSON Array* oleh agen AI menyebabkan gagal simpan di SQLite. Input *array* kini dinormalisasi secara otomatis menjadi *comma-separated string* sebelum dimasukkan ke database.
- Konfigurasi `config.yml` kini mendukung `ai_agent_token` untuk autentikasi API eksternal.
- Total pengeluaran bulanan di dasbor dan *Expense Tracker* sekarang hanya menghitung pengeluaran yang statusnya sudah "Lunas".
