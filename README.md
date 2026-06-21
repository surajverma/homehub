[![CI/CD](https://github.com/zarvelionzynji/homehub/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/zarvelionzynji/homehub/actions/workflows/docker-publish.yml)
![Latest Release](https://img.shields.io/github/v/release/zarvelionzynji/homehub?include_prereleases)
[![GitHub last commit](https://img.shields.io/github/last-commit/zarvelionzynji/homehub)](https://github.com/zarvelionzynji/homehub/commits/main)

# 🏡 HomeHub (Fork)

> **Info Fork**: Proyek ini adalah versi modifikasi (fork) dari repositori asli [HomeHub buatan surajverma](https://github.com/surajverma/homehub). 

HomeHub adalah aplikasi web ringan yang berjalan di server lokal (self-hosted) untuk menjadi pusat kegiatan digital harian keluarga Anda.

## ✨ Fitur Utama
* **🔗 Quick Links**: Dasbor visual bergaya kotak (*grid*) untuk mengorganisasi *bookmark* dan tautan favorit keluarga, lengkap dengan logo ikon dan pengelompokan kategori pintar.
* **📝 Catatan & Cloud Bersama**: Tempat sederhana untuk menulis catatan cepat dan berbagi file di jaringan rumah.
* **🛒 Daftar Belanja & Tugas (Chores)**: Daftar kolaboratif untuk melacak belanjaan dan tugas rumah tangga.
* **🗓️ Kalender & Pengingat**: Kalender bersama untuk mengingat tanggal penting.
* **💰 Pencatat Pengeluaran**: Lacak pengeluaran keluarga dan tagihan bulanan (mendukung status lunas/belum bayar).
* **👋 Siapa di Rumah?**: Cek dengan cepat siapa anggota keluarga yang sedang berada di rumah.
* **🎬 Pengunduh Media**: Simpan video atau musik langsung ke server Anda.
* **🤖 AI Agent API (RESTful)**: Hubungkan HomeHub dengan asisten AI favorit Anda (seperti OpenClaw atau GPT). AI dapat mengontrol status rumah, membaca catatan, menyelesaikan tugas, hingga mengubah `config.yml` secara programatis via API terenkripsi.
* **Privasi Penuh**: Semua data Anda tetap berada di jaringan Anda sendiri. Tidak ada pelacakan dari pihak ketiga.

## 🚀 Cara Menjalankan dengan Docker

1. Clone repositori ini dan buat salinan konfigurasi:
```bash
git clone https://github.com/zarvelionzynji/homehub.git
cd homehub
cp config-example.yml config.yml
```

2. Buka dan edit `config.yml` untuk mengatur nama anggota keluarga dan pengaturan lainnya.

3. Jalankan dengan Docker Compose:
```bash
docker compose up -d
```
HomeHub Anda siap diakses di [http://localhost:5000](http://localhost:5000).

## 🛠️ Cara Pengembangan (Development)

Jika Anda ingin menjalankannya secara lokal tanpa Docker untuk mengubah kode:
```bash
python -m venv venv
venv\Scripts\activate  # Di Windows
pip install -r requirements.txt
npm install
npm run build:css

python run.py
```

## 📜 Lisensi & Penafian (Disclaimer)

Proyek ini dilisensikan di bawah MIT License. 
Perangkat lunak ini disediakan "apa adanya" tanpa garansi. Dirancang khusus untuk penggunaan di **jaringan lokal yang terpercaya** dan tidak disarankan untuk diekspos langsung ke internet publik tanpa pengamanan tambahan.
