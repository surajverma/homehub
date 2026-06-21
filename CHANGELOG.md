# Changelog

Semua perubahan yang signifikan pada proyek HomeHub ini akan dicatat di file ini.
Format penulisan berdasarkan [Keep a Changelog](https://keepachangelog.com/id/1.0.0/).

## [v1.0.1] - 2026-06-21

### Added
- **Dedicated Recurring Expenses Page**: Pengaturan tagihan berulang dan pengaturan global pengeluaran dipindahkan dari *pop-up modal* ke halaman khusus (`/expenses/recurring`) dengan navigasi *tabs* (Recurring Rules & General Settings) yang lebih luas dan rapi.
- **Edit Strategy for Recurring Rules**: Tiga opsi strategi aman saat mengedit aturan berulang (*Apply from effective date*, *Split rule*, *Rewrite all*) untuk mencegah terhapusnya riwayat tagihan lama yang sudah dicetak.
- **Grouped Expense Sidebar**: Pengelompokan tampilan daftar pengeluaran di sidebar kalender `/expenses` berdasarkan tipe (*recurring* dengan badge biru, manual dengan badge abu-abu) dengan *checkbox bulk-delete* pintar per kelompok.
- **Drag & Drop Quick Links**: Menambahkan fitur pengurutan (Sortable.js) pada menu *Manage Quick Links*. Tautan dan kategori sekarang bisa digeser (drag and drop) dan urutannya akan tersimpan secara persisten ke database (penambahan tabel `quick_link_category` dan kolom `order_index`).
- **Quick Links (Dashboard Bookmark)**: Fitur baru untuk menyimpan tautan akses cepat (seperti Heimdall/Homarr mini). Mendukung manajemen ikon cerdas (SVG CDN atau Favicon) dengan pengelompokan kategori bergaya kotak (*Grid*) vertikal/horizontal langsung di *dashboard* utama. Dilengkapi sistem *Feature Toggle* (dapat dinonaktifkan di `config.yml`) dan kemampuan CRUD penuh oleh asisten AI lewat aksi `edit_quick_link`, dsb.
- **Delete Actions untuk AI Router**: AI sekarang bisa menghapus catatan, tugas, dan barang belanjaan lewat aksi `delete_note`, `delete_chore`, dan `delete_shopping_item`.
- **AI Universal Router Expansion**: Penambahan fungsi asisten AI untuk membaca/memanipulasi `config.yml` (Config API) serta memanipulasi Catatan (*Notes*), Tugas Rumah (*Chores*), dan Daftar Belanja (*Shopping List*) via `POST /api/ai/execute`.
- **AI Agent Universal Router**: API Endpoint (`/api/ai/execute`) tunggal untuk memungkinkan asisten AI pihak ketiga berinteraksi dengan seluruh *database* dan sistem *HomeHub* secara tersentralisasi.
- **Auto-Schema AI**: Endpoint (`/api/ai/schema`) yang mengembalikan format JSON OpenAI-compatible agar pengaturan *tool* AI lebih mudah.
- **Status Lunas/Belum Bayar (Expenses)**: Kolom `is_paid` pada pengeluaran dan penandaan visual (Lunas/Belum Bayar) di *Expense Tracker* dan kalender.
- **Pengaturan Horizon Tagihan Bulanan**: Tagihan berulang sekarang dicetak secara proaktif sampai akhir bulan saat ini untuk memudahkan perencanaan keuangan.

### Changed
- **UI/UX Dark Mode Enhancements**: Meningkatkan dukungan tema gelap (*Dark Mode*) untuk halaman *Recurring Expenses* dengan mengubah CSS statis menjadi kelas *utility Tailwind* (seperti `dark:bg-gray-800`).
- **Bug Fix (Early Payment Tracking)**: Memperbaiki deteksi pelunasan tagihan berulang bulanan di *dashboard* agar tetap mendeteksi pembayaran yang dilakukan sangat awal di bulan yang sama (sebelumnya gagal mendeteksi jika selisih pembayaran lebih dari 20 hari).
- **Bug Fix (Overlap Layout)**: Memperbaiki elemen yang saling tumpang tindih (*overlap*) antara judul pengeluaran yang panjang dan label *badge* pada sidebar dengan menerapkan *flex constraints* (`shrink-0` dan `min-w-0`).
- **Bug Fix (UnboundLocalError)**: Memperbaiki *Internal Server Error 500* pada dasbor (khususnya *widget* *Reminder*) yang diakibatkan oleh *shadowing variable* `timedelta` lokal pada Python.
- **Bug Fix (Dashboard Clock)**: Memperbaiki masalah di mana jam berjalan di halaman muka tidak mengindahkan pengaturan format 24 jam (`reminders.time_format`) pada `config.yml`. Jam utama dan kartu sambutan sekarang mendetek format waktu dengan benar serta detiknya terus berdetak tanpa memuat ulang halaman.
- **Bug Fix (AI Tags Array)**: Memperbaiki masalah di mana pengiriman `tags` berupa *JSON Array* oleh agen AI menyebabkan gagal simpan di SQLite. Input *array* kini dinormalisasi secara otomatis menjadi *comma-separated string* sebelum dimasukkan ke database.
- Konfigurasi `config.yml` kini mendukung `ai_agent_token` untuk autentikasi API eksternal.
- Total pengeluaran bulanan di dasbor dan *Expense Tracker* sekarang hanya menghitung pengeluaran yang statusnya sudah "Lunas".
