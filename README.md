# Business Development Analytics Dashboard

**Dashboard analitik untuk tim Business Development AIESEC in BINUS.**

Proyek ini menyatukan pemantauan aktivitas market research, perkembangan partnership, dan kontribusi partner dalam satu dashboard interaktif. Dirancang untuk membantu tim melihat pencapaian, mengevaluasi progres, serta menentukan tindak lanjut melalui ringkasan metrik dan visualisasi yang mudah dipahami.

## Tentang Proyek

Pengelolaan partnership mencakup banyak hal: mencari calon partner, memantau proses kerja sama, mencatat kontribusi, hingga memastikan dokumen dan kontrak tetap terpantau. Dashboard ini memberikan gambaran menyeluruh atas aktivitas tersebut agar tim lebih mudah memahami kondisi Business Development pada periode tertentu.

## Fitur Utama

| Bagian | Yang ditampilkan |
| --- | --- |
| **Ringkasan** | Metrik utama, tren aktivitas, dan daftar partner yang membutuhkan perhatian. |
| **Market Research** | Perkembangan aktivitas riset per bulan dan kontribusi setiap PIC. |
| **Partner & Funnel** | Jumlah partner aktif, komposisi stakeholder, tahapan sales funnel, dan conversion rate. |
| **Revenue** | Financial Revenue dan In-Kind Value, beserta rincian per bulan dan per partner. |
| **Dokumen & Kontrak** | Kelengkapan dokumen kerja sama, status kontrak, dan masa berakhirnya partnership. |

Dashboard juga dilengkapi dengan:

- **Filter periode** untuk melihat seluruh periode, kuartal, atau bulan tertentu.
- **Grafik interaktif** untuk menjelajahi tren dan melihat detail nilai.
- **Penanda tindak lanjut** untuk dokumen yang belum lengkap dan kontrak yang mendekati akhir masa berlaku.
- **Pilihan akses** berupa tampilan Anonymous dengan penyamaran data dan Member Login untuk melihat informasi internal.

## Metrik yang Dipantau

- **Market Research:** aktivitas riset calon partner yang dilakukan tim.
- **Active Partners:** partner dengan kerja sama yang masih aktif.
- **Conversion Rate:** persentase konversi pada tahap penandatanganan kontrak.
- **Financial Revenue:** kontribusi finansial dari partner.
- **In-Kind Value:** nilai dukungan berupa barang atau jasa dari partner.

Financial Revenue dan In-Kind Value ditampilkan secara terpisah agar kontribusi uang dan dukungan nonuang dapat dibaca dengan jelas.

## Teknologi

Dibangun menggunakan **Python**, **Streamlit** untuk antarmuka dashboard, **Pandas** untuk pengolahan data, dan **Plotly** untuk visualisasi interaktif.

## Menjalankan Secara Lokal

Setelah mengunduh atau melakukan clone repository, buka terminal di folder proyek. Contoh berikut menggunakan PowerShell di Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Lengkapi `.env` sesuai [template konfigurasi](.env.example), letakkan kredensial Google Service Account pada `credentials.json`, dan pastikan akun tersebut memiliki akses Viewer ke spreadsheet yang digunakan. Untuk Member Login, atur `MEMBER_PASSWORD` di `.streamlit/secrets.toml`.

Jalankan dashboard:

```powershell
python -m streamlit run app.py
```

> Aplikasi memerlukan konfigurasi sumber data internal, termasuk saat menggunakan tampilan Anonymous. Mode ini merupakan pratinjau dengan penyamaran data, bukan demo mandiri yang dapat dijalankan tanpa konfigurasi.

## Struktur Singkat

```text
bd-dashboard/
├── app.py             # Antarmuka dan interaksi dashboard
├── src/               # Modul analitik dan visualisasi
├── notebooks/         # Eksplorasi dan analisis
├── assets/            # Folder aset visual
├── data/sample/       # Folder yang disiapkan untuk data contoh
├── requirements.txt   # Dependensi proyek
└── .env.example       # Template konfigurasi lokal
```

## Catatan Data

Proyek ini dikembangkan untuk kebutuhan Business Development AIESEC in BINUS. Data partner, nilai kontrak, dan dokumen internal tidak disertakan dalam README ini. File konfigurasi pribadi dan kredensial juga dikecualikan dari Git melalui `.gitignore`.
