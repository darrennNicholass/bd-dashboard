# Business Development Analytics Dashboard — AIESEC in BINUS

Dashboard analitik untuk tim Business Development: memantau Market Research,
portofolio partner, sales funnel conversion rate, Financial Revenue, dan In-Kind Value.

Google Sheets dipakai **hanya sebagai sumber data live**. Analitik dan dashboard
dibangun di Python.

## Arsitektur

```
Active ESSM ────────────► Google Sheets API ──► Python/Pandas ──► df_mr ──┐
                                                                          │
National ESSM (asli)                                                      ├──► KPI ──► Plotly ──► Streamlit
      │ IMPORTRANGE                                                       │
      ▼                                                                   │
National Mirror ────────► Google Sheets API ──► Python/Pandas ──► df_partner / df_financial / df_inkind
```

## Aturan data governance

- National ESSM asli **tidak pernah** diakses langsung. Satu-satunya sumber
  National adalah mirror spreadsheet (`NATIONAL_SOURCE_URL`).
- Service Account memakai scope **read-only**; dashboard tidak bisa menulis ke ESSM.
- `credentials.json`, `.env`, dan `.streamlit/secrets.toml` tidak pernah di-commit.

### Syarat konfigurasi mirror

Ketiga tab mirror harus menarik range **`A:CZ`**, bukan `A:AZ`. Dengan `A:AZ`
kolom `Link Invoice`, `Month of Signed`, dan `Month End` tidak ikut tertarik,
sehingga KPI Active Partners dan Expiry Tracker tidak bisa dihitung.

Periksa kapan pun dengan:

```powershell
python inspect_mirror_range.py
```

Script itu hanya menampilkan bagian range dan menyensor URL spreadsheet sumber.

## Struktur proyek

```
bd-dashboard/
├── app.py                  # Phase 11: dashboard Streamlit
├── verify_setup.py         # Phase 1: verifikasi environment & koneksi
├── inspect_active.py       # Phase 2: inspeksi struktur Active ESSM
├── build_mr.py             # Phase 3: bangun & validasi df_mr
├── inspect_national.py     # Phase 4/6/7: inspeksi struktur National Mirror
├── inspect_mirror_range.py # cek range IMPORTRANGE mirror (URL disensor)
├── build_partner.py        # Phase 5: df_partner + df_conversion
├── build_revenue.py        # Phase 6/7: df_financial + df_inkind
├── build_kpi.py            # Phase 8: jalankan & validasi seluruh KPI
├── build_period.py         # Phase 9: validasi filter periode
├── build_charts.py         # Phase 10: bangun & validasi figure Plotly
├── build_app.py            # Phase 11: jalankan app headless & validasi angkanya
├── .streamlit/
│   └── config.toml       # tema + bind ke localhost (tanpa rahasia)
├── requirements.txt
├── .env.example
├── src/
│   ├── sheets.py         # ekstraksi Google Sheets (satu-satunya akses API)
│   ├── preparation.py    # penyiapan & validasi dataframe
│   ├── metrics.py        # perhitungan KPI
│   ├── periods.py        # filter periode (bulan / kuartal)
│   └── charts.py         # visualisasi Plotly
├── notebooks/
│   └── exploration.ipynb
├── data/sample/          # sample dataset anonim (versi portfolio)
└── assets/
```

## Aturan bisnis df_mr

Ditetapkan setelah inspeksi Phase 2 dan dikonfirmasi tim BD:

| Aturan | Keputusan | Alasan |
|---|---|---|
| Sumber `month` | baris penanda bulan di kolom NO | `DATE OF MR` hanya terisi 37,9% dan sebagian diisi massal dengan tanggal identik |
| Baris record | kolom NO berisi angka **dan** nama partner terisi | menyaring baris legend, summary, dan penanda bulan |
| Cakupan bulan | April ke atas; February & March dibuang | isi Feb & Mar hasil copy-paste ke ketiga tab sehingga terhitung ganda |
| Dedupe antar tab | tidak dilakukan | ELDs/EWAs/SS adalah tiga tim berbeda, partner sama = aktivitas MR terpisah |
| Total MR | jumlah kemunculan nama PIC | definisi tim BD |

## Aturan bisnis df_partner

Ditetapkan setelah inspeksi Phase 4 dan dikonfirmasi tim BD:

| Aturan | Keputusan |
|---|---|
| Header | berlapis tiga (baris 1, 2, 3); kolom dicari di ketiga baris |
| Kolom dokumen | ketiganya berlabel `Link`; dibedakan dari tahap funnel sebelumnya |
| Dokumen ada | sel terisi **dan** bukan `-` |
| `Month End` | hanya level bulan; diperlakukan sebagai **akhir bulan** |
| Active Partner | punya dokumen **LoA** dan `Month End >= bulan berjalan` |
| Kolom `Status` sheet | tidak dipakai untuk KPI (masih Active walau kontrak habis) |
| Cakupan bulan | February & March **tetap dipakai** (satu tab, tidak ada hitung ganda) |
| Conversion rate | diambil dari sheet (tahap `5. Contract Signed`), tidak dihitung ulang |

## Aturan bisnis df_financial & df_inkind

National 1.3 dan 1.4 strukturnya identik, jadi memakai parser yang sama.

| Aturan | Keputusan |
|---|---|
| Pembagian section | per **kuartal** (`QUARTER #1`…`#4`), bukan per bulan |
| Sumber bulan | kolom `MONTHS` (selalu terisi), bukan `Date Received` |
| Format `Date Received` | campur M/D/Y dan D/M/Y; diurai lalu dicocokkan `MONTHS`, sisanya pakai format **dominan per tab** |
| Nilai uang | `Rp26,750,000.00` → `26750000.0`; nilai mentah tetap disimpan |
| Financial vs In-Kind | **tidak pernah** dijumlahkan menjadi satu angka |

Total hasil perhitungan dicek silang terhadap total yang sudah dihitung
sheet (baris 3 dan tiap baris kuartal). Semua cocok.

## Aturan layer KPI (`src/metrics.py`)

Ditetapkan di Phase 8. Semua fungsi menerima dataframe sebagai argumen,
jadi bisa diuji tanpa koneksi Google Sheets dan tanpa Streamlit.

| Aturan | Keputusan |
|---|---|
| Total MR | jumlah kemunculan nama PIC, bukan jumlah baris atau partner unik |
| Active Partners | kolom `is_active` dari df_partner; kolom `Status` sheet tidak dipakai |
| Conversion rate | dibaca dari df_conversion tahap `5. contract signed`, tidak dihitung ulang |
| Financial vs In-Kind | dua fungsi terpisah; `get_kpi_summary()` sengaja **tanpa** kunci `total_revenue` |
| Document Tracker | hanya partner **aktif** |
| Expiry Tracker | berbasis bulan; kategori `expired`, `berakhir bulan ini`, `1-3 bulan lagi`, `lebih dari 3 bulan`, `tanpa data` |
| Nilai tidak ada | dikembalikan NaN / `pd.NA`, tidak pernah diganti 0 |
| Kolom hilang | `KeyError` dengan pesan jelas, bukan KPI yang diam-diam salah |

Validasi Phase 8 (`python build_kpi.py`) mencocokkan enam angka headline dengan
hasil validasi BD di Phase 3–7 dan akan keluar dengan status gagal kalau ada
yang bergeser:

```
total_mr           1694
partner_count      35
active_partners    16
conversion_rate    82.86%
financial_revenue  Rp26.750.000
inkind_value       Rp119.685.000
```

Selain itu script memeriksa konsistensi agregasi (MR per PIC = MR per bulan =
Total MR; revenue per bulan = per partner = total) dan perilaku layer
(dataframe kosong, scope tidak ada, kolom kurang, pergeseran `reference_date`).

## Aturan filter periode (`src/periods.py`)

Ditetapkan di Phase 9. Satuan terkecil adalah **bulan**, karena sumber
(penanda bulan, kolom `MONTHS`, `Month End` berformat `SEP 26`) tidak pernah
memberi level harian.

Perbedaan penting: KPI dibagi dua jenis, dan filter memperlakukannya berbeda.

| Jenis | KPI | Perlakuan filter |
|---|---|---|
| **Aliran** | Total MR, Financial Revenue, In-Kind Value, partner baru | baris di luar periode dibuang; nilainya additif |
| **Posisi** | Active Partners, Document Tracker, Expiry Tracker | baris **tidak** dibuang; `reference_date` digeser ke akhir periode |

Kalau df_partner ikut disaring per bulan, Active Partners berubah arti jadi
"partner yang didaftarkan bulan itu **dan** aktif" — angka yang tidak pernah
diminta tim BD. Karena itu metrik posisi dihitung ulang lewat
`preparation.apply_reference_date()`.

| Aturan | Keputusan |
|---|---|
| Urutan bulan | masa jabatan: February → … → January (Januari tahun berikutnya) |
| Kuartal | Q1 Feb–Apr, Q2 Mei–Jul, Q3 Agu–Okt, Q4 Nov–Jan (dicek silang dengan kolom `quarter` National 1.3/1.4) |
| Tanggal acuan | akhir bulan terakhir periode, **tidak pernah** melewati hari ini |
| Conversion rate | seluruh periode → scope `all`; satu bulan → scope bulan itu; gabungan beberapa bulan → **NaN** + catatan, karena sheet tidak menyediakannya dan aturan melarang hitung ulang |
| February & March | sah dipilih untuk partner & revenue; Total MR-nya 0 (dibuang di Phase 3) disertai catatan |
| Bulan tidak dikenal | `ValueError`, bukan hasil filter kosong yang menipu |

`apply_period()` adalah satu-satunya tempat pilihan periode diterjemahkan,
supaya semua KPI di dashboard pasti memakai periode yang sama.

Validasi Phase 9 (`python build_period.py`) memeriksa:

- seluruh periode mereproduksi enam angka baseline Phase 3–8
- additivitas: jumlah 12 bulan = total (MR 1694, partner 35, revenue, in-kind)
- kuartal hasil filter bulan = kolom `quarter` di sheet (8 perbandingan cocok)
- conversion rate per bulan identik dengan nilai sheet; gabungan bulan → NaN
- Active Partners tidak pernah naik saat tanggal acuan maju
  (22 di Februari → 18 Juli → 17 Agustus → 16 per hari ini)
- perilaku tepi: bulan tanpa data, alias bulan Indonesia, pilihan ganda,
  bulan/kuartal ngawur, dataframe kosong, kolom `month` hilang

## Aturan layer visualisasi (`src/charts.py`)

Ditetapkan di Phase 10. Setiap fungsi menerima dataframe hasil `metrics.py`
dan mengembalikan satu figure Plotly; tidak ada query data maupun perhitungan
KPI di layer ini.

| Aturan | Keputusan |
|---|---|
| Financial vs In-Kind | ditampilkan berdampingan dengan `barmode='group'`; **stack dilarang** karena tinggi totalnya akan terbaca sebagai penjumlahan |
| Stack yang boleh | hanya Kelengkapan Dokumen (ada + belum = partner aktif) dan Kontrak per Bulan Berakhir; di kedua chart itu totalnya memang bermakna |
| Palet | satu keluarga **biru** (permintaan tim BD): navy → indigo → biru langit; kategori dibedakan lewat kegelapan, bukan hue |
| Warna | **tidak pernah** menjadi satu-satunya pembawa informasi — karena palet sewarna, setiap elemen wajib punya label teks |
| Bulan tanpa data | untuk MR, garis area berhenti di bulan terakhir yang berisi (bukan turun ke 0, yang terbaca sebagai anjlok) |
| Bulan tanpa conversion rate | titik dibiarkan bolong (`connectgaps=False`), tidak digambar 0 |
| Periode tanpa conversion rate | figure menampilkan "Sheet tidak menyediakan conversion rate untuk periode ini", bukan angka |
| Dataframe kosong | figure berisi keterangan, bukan exception atau kanvas kosong |
| Kolom kurang | `KeyError`, supaya tidak ada grafik yang menyesatkan |
| Gaya | chrome minimal: tanpa garis sumbu, grid tipis, sumbu nilai dihilangkan kalau angkanya sudah menempel di elemen |
| Label uang | bentuk ringkas di grafik (`Rp26,8 jt`), nilai penuh di hover |

Enam belas figure: MR per PIC, MR per bulan (bar & area), gauge partner aktif,
donut stakeholder, sales funnel, conversion per bulan, Financial & In-Kind per
bulan, perbandingan keduanya, Financial & In-Kind per partner, kelengkapan
dokumen, document tracker (heatmap), status kontrak, dan kontrak per bulan
berakhir.

Validasi Phase 10 (`python build_charts.py`) memeriksa **isi** figure, bukan
hanya apakah figure terbentuk: jumlah nilai di setiap trace harus sama dengan
angka dari `metrics.py` (MR 1694, partner 35, revenue, in-kind, 16 baris
heatmap, funnel berakhir di 82,86 %). Script juga menulis
`phase10_charts.html` untuk pemeriksaan visual — file itu memuat nama partner,
jadi ikut diabaikan Git lewat pola `phase*.html`.

## Dashboard (`app.py`)

Dibangun di Phase 11. `app.py` hanya mengatur tata letak dan interaksi; tidak
ada aturan bisnis baru di sana.

```powershell
.\.venv\Scripts\streamlit.exe run app.py
```

| Bagian | Isi |
|---|---|
| Sidebar | brand mark, pilihan rentang (Semua / Kuartal / Bulan), tombol muat ulang data |
| Kartu KPI | Market Research, Active Partners, Conversion Rate, Financial Revenue, In-Kind Value — lima kartu bersparkline, **tanpa** kartu gabungan |
| Pill status | kontrak aktif berakhir bulan ini, kontrak 1–3 bulan lagi, dokumen yang belum ada, catatan periode |
| Ringkasan | area chart MR (fokus utama), kartu gelap gauge partner aktif, panel "Perlu perhatian", lalu funnel · status kontrak · kelengkapan dokumen |
| Tab | Ringkasan · Market Research · Partner & Funnel · Revenue · Dokumen & Kontrak |

### Arah desain

Mengikuti referensi yang diberikan tim BD: SaaS dashboard soft-indigo.

| Elemen | Keputusan |
|---|---|
| Latar & kartu | latar lavender `#F4F6FC`, kartu putih radius 20 px, border tipis, bayangan halus — bukan kotak bergaris tegas |
| Aksen | indigo `#5B6BF7`; satu kartu gelap bergradien indigo sebagai penarik mata |
| Palet plot | satu keluarga biru (navy → indigo → biru langit), lihat aturan `charts.py` |
| Tipografi | dua keluarga font supaya tidak monoton: **Plus Jakarta Sans** (judul & angka) + **Inter** (teks), diatur lewat `theme.headingFont` dan `theme.font` |
| Hierarki | label KPI kecil huruf kapital berspasi, angka besar tebal, keterangan abu kecil |
| Sparkline | `st.metric(chart_data=...)` di setiap kartu KPI; warnanya ikut keluarga biru |

| Aturan | Keputusan |
|---|---|
| Cache | `st.cache_data` TTL 15 menit; data ESSM diperbarui manual jadi tidak perlu lebih sering, sekaligus menghemat kuota API |
| Nilai uang | kartu & label grafik memakai bentuk ringkas (`Rp26,8 jt`), nilai penuh ada di tooltip dan hover |
| Conversion rate kosong | ditampilkan `—` disertai keterangan, tidak pernah 0 |
| Delta kartu KPI | tanpa panah (`delta_arrow="off"`), karena isinya keterangan ("11 PIC terlibat"), bukan perubahan |
| Nama partner di HTML | selalu lewat `html.escape()`; isi spreadsheet tidak diperlakukan sebagai markup |
| Gaya grafik | modebar Plotly disembunyikan, sumbu nilai dihilangkan kalau angkanya sudah menempel di elemen |
| Kegagalan koneksi | pesan yang menyebut `.env` / `credentials.json` / akses Viewer, bukan traceback mentah saja |

**Keamanan:** dashboard ini tidak punya autentikasi dan menampilkan nama
partner serta nilai kontrak. `.streamlit/config.toml` mengikat server ke
`127.0.0.1` supaya sesi lokal tidak ikut terekspos ke jaringan. Sebelum
dideploy (Phase 13) wajib dipasangi proteksi akses.

Validasi Phase 11 (`python build_app.py`) menjalankan `app.py` headless lewat
`streamlit.testing.AppTest`, lalu membandingkan angka yang benar-benar tampil
di kartu KPI dengan hasil hitungan `metrics.py` untuk tiga periode:

```
seluruh periode  1.694 | 16 | 82,86%  | Rp26,8 jt  | Rp119,7 jt
Agustus            355 | 17 | 100,00% | Rp5,5 jt   | Rp2,3 jt
QUARTER #2       1.124 | 18 | —       | Rp19,5 jt  | Rp110,3 jt
```

Script itu juga memastikan tidak ada kartu "Total Revenue", halaman menyebut
kedua KPI revenue tidak dijumlahkan, dan URL spreadsheet tidak pernah tercetak
ke halaman.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # lalu isi nilainya
```

Letakkan `credentials.json` (Google Service Account) di root proyek, dan beri
akses **Viewer** ke Active ESSM serta National Mirror.

## Verifikasi koneksi

```powershell
python verify_setup.py
```

Script ini melaporkan status konfigurasi sebagai `YES/NO` saja dan tidak pernah
mencetak URL maupun isi credential.

## Progress

- [x] Phase 1 — Verify project & connection
- [x] Phase 2 — Inspect Active ESSM
- [x] Phase 3 — Build `df_mr` (tervalidasi BD: Total MR = 1694)
- [x] Phase 4 — Inspect National 1.1 (selesai; mirror diperlebar ke `A:CZ`)
- [x] Phase 5 — Partner data & conversion rate (tervalidasi: Active Partners = 16)
- [x] Phase 6 — Financial revenue (tervalidasi: Rp26.750.000)
- [x] Phase 7 — In-kind value (tervalidasi: Rp119.685.000)
- [x] Phase 8 — KPI layer (`src/metrics.py`; enam angka headline cocok)
- [x] Phase 9 — Period filter (`src/periods.py`; baseline & additivitas cocok)
- [x] Phase 10 — Visualization (`src/charts.py`; 14 figure, isi figure tervalidasi)
- [x] Phase 11 — Streamlit (`app.py`; angka di kartu KPI tervalidasi headless)
- [ ] Phase 12 — QA  <-- LANJUT DI SINI
- [ ] Phase 13 — Deployment
- [ ] Phase 14 — Portfolio version
