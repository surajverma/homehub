# Changelog

Semua perubahan yang signifikan pada proyek HomeHub ini akan dicatat di file ini.
Format penulisan berdasarkan [Keep a Changelog](https://keepachangelog.com/id/1.0.0/).

## [v1.0.5] - 2026-06-26

### Added
- **PWA Web Share Target API**: Mengintegrasikan API Web Share Target ke dalam PWA HomeHub. Pengguna Android (dan desktop yang kompatibel) kini dapat menggunakan fitur "Share" bawaan OS (misalnya dari YouTube atau TikTok) untuk membagikan tautan video secara langsung ke aplikasi HomeHub. 
- **Media Pre-fill**: Tautan yang dibagikan melalui *Share sheet* kini akan langsung membuka halaman Media Downloader dan otomatis mengisi (*pre-fill*) kolom input URL. Pengguna bisa me-*review* URL, memilih format (MP4/MP3) atau kualitas yang diinginkan sebelum mengklik Download.

## [v1.0.4] - 2026-06-23

### Added
- **AI Attachment Support (Base64)**: Endpoint AI agent (`POST /api/ai/execute`) kini mendukung unggahan lampiran struk/bukti bayar berbasis Base64 untuk modul pengeluaran. Menambahkan parameter fungsi `attachment_base64` dan `attachment_filename` yang otomatis diproses, dikompresi dengan Pillow, dan disimpan secara persisten.

### Changed
- **AI Metadata Lampiran**: Response endpoint pembacaan data (`get_expenses` dan `get_recurring_expenses`) kini menyertakan field `has_attachment` (boolean) dan `attachment_path` (string) sebagai indikator keberadaan lampiran.

### Fixed
- **AI Attachment Validation**: Menambahkan validasi ketat untuk lampiran expense: (1) Invalid base64 ditolak dengan error "Invalid base64 string", (2) attachment_base64 dan attachment_filename wajib berpasangan, ditolak dengan error "Both attachment_base64 and attachment_filename are required if one is provided".
- **Attachment Semantics**: Mendokumentasikan perbedaan semantik lampiran di schema: expense individual = bukti pembayaran per-transaksi, recurring expense = bukti kontrak/langganan (template, tidak di-copy ke generated entries).
- **Expense Delete Confirm**: Memperbaiki bug UX di mana konfirmasi penghapusan pengeluaran satuan dan penghapusan massal (*bulk delete*) tidak muncul karena konflik cakupan fungsi lokal (*scope ReferenceError*) pada *inline handler*. Dialog konfirmasi kini muncul dengan benar sebelum mengeksekusi penghapusan.

## [v1.0.3] - 2026-06-22

### Fixed
- **Quick Links Edit Modal Centering**: Memperbaiki bug pada Edit Quick Link Modal (`quick_links.html`) yang tampil di pojok kiri atas pada desktop dan di paling atas pada mobile. Dengan menghapus kelas `flex` bawaan dari deklarasi HTML awal (dan menyisakan kelas `hidden`), perubahan kelas dinamis JavaScript (`classList.add('flex')` dan `classList.remove('hidden')`) kini memposisikan modal di tengah-tengah layar secara presisi baik secara vertikal maupun horizontal di semua ukuran viewport.
- **Expense Editing & Navigation Flow**:
  - Membuka pembatasan akses edit/delete pengeluaran individu di backend (`expenses.py`) dan frontend (`expenses.html`) agar semua anggota keluarga yang sah (bukan hanya pembuat/admin) dapat mengedit atau menghapusnya. Ini sangat krusial bagi entri pengeluaran rutin (*recurring*) yang dibuat oleh admin tetapi perlu ditandai sebagai 'Lunas' atau dilampirkan bukti bayar oleh anggota keluarga lain.
  - Menjadikan daftar baris pengeluaran bulanan di sidebar "Entries This Month" dapat diklik (*clickable*). Mengeklik baris pengeluaran kini akan secara otomatis menggeser fokus tanggal kalender dan membuka panel detail di sebelah kiri, mempermudah akses pengeditan/penghapusan langsung dari ringkasan bulanan.

## [v1.0.2] - 2026-06-22

### Changed
- **Premium UI/UX Refactoring**: Menyelesaikan seluruh 4 fase refaktor UI/UX berstandar tinggi (ui-ux-expert + ui-ux-pro-max) mencakup perbaikan aksesibilitas, konsistensi visual, kegunaan perangkat seluler (mobile), transisi halus, dan kelengkapan tema gelap (dark mode).
- **A11y (Accessibility) Improvements**: Menambahkan tag `<label>` terikat untuk semua input form yang hilang (seperti User Switcher, status form, input catatan, modal pemilihan user). Menambahkan ikon pendamping visual untuk pill status "Who is Home" agar tidak bergantung pada warna saja. Menambahkan `:focus-visible` ring bergaya premium pada elemen interaktif yang bisa dinavigasi dengan keyboard.
- **Mobile & Touch Optimization**: Memperbesar touch target (ukuran tap minimum 44x44px) untuk semua tombol filter rentang waktu (scope), filter kategori pengingat, navigasi bulan kalender, dan sel hari kalender. Menambahkan *backdrop overlay* interaktif pada menu sidebar seluler agar bisa ditutup dengan menyentuh area luar.
- **Async Interaction State**: Menghindari double-submit pada aksi AJAX (Who is Home update/clear dan Personal Status save) dengan menambahkan status `disabled` dan ikon pemutar (*spinner animation*) pada tombol selama request berlangsung.
- **Dark Mode Completion**: Mengisi celah contrast dan warna hardcoded pada modal pemilihan user awal, tab menu pengeluaran, dan latar belakang manajemen tautan cepat (*Quick Links*) agar menyatu sempurna dalam data-theme gelap.
- **Micro-Animations & Motion**: Mengganti transition generik `all` dengan properti transisi spesifik (gpu-friendly) untuk card dan sidebar-link. Menerapkan hover efek mengangkat (`translateY(-2px)`) yang lebih premium pada kartu dasbor. Menghargai preferensi sistem reduced motion via `@media (prefers-reduced-motion: reduce)`.
- **Icon Standardization**: Menghapus penggunaan emoji unicode `🧾 ◀ ▶ ✕ ✎` di seluruh layout dan menggantinya dengan ikon Font Awesome (chevron, trash-can, pen, receipt) yang konsisten.
- **Heading Hierarchy Fixes**: Menata ulang struktur heading (`h2` ke `h1`) pada sub-halaman Shopping List, Chores, Recipe Book, dan Quick Links untuk mematuhi SEO best practices.
- **Script & Layout Consolidation**: Merapikan peletakan tag `<script>` ke dalam batas tag `<body>` sebelum penutup dan menyatukan penulisan `.btn` ke dalam `static/input.css` (membersihkan style inline yang duplikat).

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
- **API (AI Agent Validations)**: Memperbaiki sejumlah celah validasi pada rute API asisten AI, termasuk memblokir nominal negatif, membatasi input tahun (2000-2100), memastikan tanggal mulai tidak melebihi tanggal akhir pada tagihan berulang, mewajibkan pengisian nama pembayar (*payer*), serta memperbaiki logika konsistensi *hard-delete* untuk aturan berulang.
- **Bug Fix (Overlap Layout)**: Memperbaiki elemen yang saling tumpang tindih (*overlap*) antara judul pengeluaran yang panjang dan label *badge* pada sidebar dengan menerapkan *flex constraints* (`shrink-0` dan `min-w-0`).
- **Bug Fix (UnboundLocalError)**: Memperbaiki *Internal Server Error 500* pada dasbor (khususnya *widget* *Reminder*) yang diakibatkan oleh *shadowing variable* `timedelta` lokal pada Python.
- **Bug Fix (Dashboard Clock)**: Memperbaiki masalah di mana jam berjalan di halaman muka tidak mengindahkan pengaturan format 24 jam (`reminders.time_format`) pada `config.yml`. Jam utama dan kartu sambutan sekarang mendetek format waktu dengan benar serta detiknya terus berdetak tanpa memuat ulang halaman.
- **Bug Fix (AI Tags Array)**: Memperbaiki masalah di mana pengiriman `tags` berupa *JSON Array* oleh agen AI menyebabkan gagal simpan di SQLite. Input *array* kini dinormalisasi secara otomatis menjadi *comma-separated string* sebelum dimasukkan ke database.
- Konfigurasi `config.yml` kini mendukung `ai_agent_token` untuk autentikasi API eksternal.
- Total pengeluaran bulanan di dasbor dan *Expense Tracker* sekarang hanya menghitung pengeluaran yang statusnya sudah "Lunas".
