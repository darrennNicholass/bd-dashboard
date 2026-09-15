"""
charts.py â€” VISUALIZATION LAYER (Phase 10, digayakan ulang di Phase 11)

Menerima dataframe yang SUDAH dihitung metrics.py dan mengembalikan figure
Plotly. Tidak ada query data dan tidak ada perhitungan KPI di sini: kalau
sebuah angka perlu dihitung, tempatnya di metrics.py.

--------------------------------------------------------------------------
ATURAN LAYER VISUALISASI
--------------------------------------------------------------------------
1. Satu fungsi = satu figure. Fungsi menerima dataframe, bukan pilihan
   periode atau koneksi Sheets.

2. Financial Revenue dan In-Kind Value boleh ditampilkan BERDAMPINGAN,
   tapi selalu barmode='group' dan NEVER 'stack'. Bar bertumpuk membaca
   tingginya sebagai jumlah kedua angka, dan penjumlahan itu dilarang
   aturan bisnis.

3. Warna tidak pernah menjadi satu-satunya pembawa informasi. Setiap
   figure juga memberi label teks/hover, supaya tetap terbaca oleh
   pengguna dengan buta warna maupun pembaca layar.

4. Palet memakai satu keluarga BIRU (permintaan tim BD): kategori
   dibedakan lewat tingkat kegelapan, bukan warna yang berbeda. Karena
   pembeda hue hilang, aturan 3 jadi makin wajib â€” setiap elemen selalu
   punya label teks.

5. Dataframe kosong menghasilkan figure berisi keterangan "tidak ada
   data", bukan exception dan bukan grafik kosong tanpa penjelasan.

6. Kolom yang kurang menghasilkan KeyError dengan pesan jelas.

--------------------------------------------------------------------------
GAYA VISUAL (Phase 11)
--------------------------------------------------------------------------
Figure dirancang untuk dipasang di kartu dashboard, bukan dibaca sendirian:

  - chrome seminimal mungkin: tanpa garis sumbu tebal, grid tipis, tanpa
    kotak legenda bergaris
  - angka ditulis di dekat elemennya (label bar / titik), sehingga sumbu
    nilai sering tidak perlu ditampilkan sama sekali
  - nilai uang di label dipendekkan ("Rp26,8 jt"); nilai penuhnya tetap
    muncul di hover
  - `title=None` dan `description=""` mematikan judul/keterangan di dalam
    figure, dipakai kalau kartu dashboard sudah punya judul sendiri
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .metrics import EXPIRY_CATEGORIES

# ---------------------------------------------------------------------------
# Palet & gaya
# ---------------------------------------------------------------------------

# Palet BIRU satu keluarga, sesuai permintaan tim BD. Dibedakan lewat
# kegelapan (bukan warna berbeda), jadi urutannya tetap terbaca oleh
# pengguna dengan buta warna â€” dan setiap elemen tetap diberi label teks.
NAVY = "#16357E"
INDIGO_DEEP = "#3B4BD8"
PRIMARY = "#5B6BF7"
PERIWINKLE = "#8492FB"
SKY = "#2E9BF0"
SKY_SOFT = "#7FC2F7"
PALE_BLUE = "#B9CEFC"
STEEL = "#8FA0C4"

PRIMARY_DEEP = INDIGO_DEEP
PRIMARY_SOFT = PERIWINKLE
PRIMARY_FILL = "rgba(91, 107, 247, 0.16)"

# Biru AIESEC tetap disimpan sebagai warna merek.
AIESEC_BLUE = "#037EF3"

# Urutan palet kategori: gelap-terang bergantian supaya potongan yang
# bersebelahan tidak pernah punya kegelapan yang mirip.
PALETTE: tuple[str, ...] = (
    PRIMARY,       # indigo
    SKY,           # biru terang
    NAVY,          # biru tua
    PERIWINKLE,    # indigo muda
    SKY_SOFT,      # biru langit muda
    INDIGO_DEEP,   # indigo tua
    PALE_BLUE,     # biru sangat muda
    STEEL,         # biru keabu
)

COLOR_FINANCIAL = PRIMARY
COLOR_INKIND = SKY
COLOR_POSITIVE = "#1F7AE0"
COLOR_MUTED = "#DDE3F1"
COLOR_TEXT = "#1B2559"
COLOR_SUBTEXT = "#8A94AD"
COLOR_GRID = "#EEF1F8"

# Kategori expiry: makin mendesak makin gelap. Label teksnya selalu ikut,
# jadi kegelapan warna hanya penguat, bukan satu-satunya penanda.
EXPIRY_COLORS: dict[str, str] = {
    EXPIRY_CATEGORIES[0]: NAVY,        # expired
    EXPIRY_CATEGORIES[1]: PRIMARY,     # berakhir bulan ini
    EXPIRY_CATEGORIES[2]: SKY,         # 1-3 bulan lagi
    EXPIRY_CATEGORIES[3]: SKY_SOFT,    # lebih dari 3 bulan
    EXPIRY_CATEGORIES[4]: STEEL,       # tanpa data
}

# Dua keluarga font supaya hierarki tulisan tidak monoton: Plus Jakarta Sans
# yang geometris untuk judul dan angka, Inter untuk teks kecil.
FONT_HEADING = "Plus Jakarta Sans, Inter, Segoe UI, system-ui, sans-serif"
FONT_FAMILY = "Inter, Plus Jakarta Sans, Segoe UI, system-ui, sans-serif"
BASE_FONT_SIZE = 12
LABEL_FONT_SIZE = 11

# Tinggi default kartu chart di dashboard. Cukup untuk dibaca, cukup pendek
# untuk memuat beberapa kartu tanpa menggulir panjang.
DEFAULT_HEIGHT = 280

EMPTY_MESSAGE = "Tidak ada data untuk periode ini"


# ---------------------------------------------------------------------------
# Util internal
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(f"{name} tidak punya kolom: {', '.join(missing)}")


def _style(
    fig: go.Figure,
    title: str | None,
    description: str = "",
    height: int | None = DEFAULT_HEIGHT,
) -> go.Figure:
    """Gaya dasar yang sama untuk semua figure.

    title None/"" -> tanpa judul di dalam figure (dipakai kalau kartu
    dashboard sudah punya judul). description "" -> tanpa baris keterangan.
    """
    top_margin = 46 if title else 16
    if description:
        top_margin += 22

    fig.update_layout(
        template="plotly_white",
        font={"family": FONT_FAMILY, "size": BASE_FONT_SIZE, "color": COLOR_TEXT},
        margin={"l": 8, "r": 12, "t": top_margin, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"font": {"family": FONT_FAMILY, "size": BASE_FONT_SIZE}},
        # Label yang tidak kebagian ruang disembunyikan, bukan diperkecil
        # sampai tidak terbaca.
        uniformtext={"minsize": 9, "mode": "hide"},
        legend={
            "orientation": "h",
            "yanchor": "bottom", "y": 1.0,
            "xanchor": "right", "x": 1.0,
            "font": {"size": LABEL_FONT_SIZE},
            "bgcolor": "rgba(0,0,0,0)",
        },
    )
    if height:
        fig.update_layout(height=height)
    if title:
        fig.update_layout(
            title={
                "text": title,
                "x": 0, "xanchor": "left",
                "y": 1, "yanchor": "top",
                "font": {
                    "family": FONT_HEADING,
                    "size": BASE_FONT_SIZE + 3,
                    "weight": 700,
                },
            }
        )
    else:
        fig.update_layout(title=None)

    if description:
        fig.add_annotation(
            text=description,
            xref="paper", yref="paper",
            x=0, y=1.0, yshift=18,
            showarrow=False, align="left",
            font={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
        )

    fig.update_xaxes(
        showgrid=False, zeroline=False, showline=False, ticks="",
        title_font={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
        tickfont={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
    )
    fig.update_yaxes(
        gridcolor=COLOR_GRID, zeroline=False, showline=False, ticks="",
        title_font={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
        tickfont={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
    )
    fig.update_layout(meta={"description": description or title or ""})
    return fig


def _empty_figure(
    title: str | None, message: str = EMPTY_MESSAGE, height: int | None = DEFAULT_HEIGHT
) -> go.Figure:
    """Figure pengganti saat tidak ada data. Menjelaskan diri sendiri."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font={"size": BASE_FONT_SIZE, "color": COLOR_SUBTEXT},
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(showlegend=False)
    return _style(fig, title, height=height)


def rupiah(value: float) -> str:
    """Format uang penuh gaya Indonesia: 26750000 -> Rp26.750.000."""
    if value is None or pd.isna(value):
        return "-"
    return "Rp" + f"{float(value):,.0f}".replace(",", ".")


def rupiah_compact(value: float) -> str:
    """Format uang ringkas untuk label grafik: 26750000 -> Rp26,8 jt.

    Nilai penuhnya tetap tersedia di hover, jadi pemendekan ini tidak
    menghilangkan informasi.
    """
    if value is None or pd.isna(value):
        return "-"
    number = float(value)
    if number == 0:
        return "Rp0"
    for divisor, unit in ((1_000_000_000, "M"), (1_000_000, "jt"), (1_000, "rb")):
        if abs(number) >= divisor:
            # Satu angka desimal selalu dipertahankan supaya Rp119.685.000
            # tidak dibulatkan menjadi "Rp120 jt" yang terlihat salah bagi BD.
            text = f"{number / divisor:.1f}"
            if text.endswith(".0"):
                text = text[:-2]
            return f"Rp{text.replace('.', ',')} {unit}"
    return rupiah(number)


def _percent(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.1f}%"


def _month_labels(values) -> list[str]:
    """Bulan dipendekkan jadi tiga huruf supaya 12 label tetap terbaca."""
    return [str(value).title()[:3] for value in values]


# ---------------------------------------------------------------------------
# Market Research
# ---------------------------------------------------------------------------


def mr_by_pic_bar(
    df_by_pic: pd.DataFrame,
    title: str | None = "Market Research per PIC",
    description: str | None = None,
) -> go.Figure:
    """Bar horizontal MR per PIC, urut dari terbanyak.

    Args:
        df_by_pic: hasil metrics.get_mr_by_pic() (pic_aiesec, total_mr).
    """
    if df_by_pic is None or df_by_pic.empty:
        return _empty_figure(title)

    _require_columns(df_by_pic, ("pic_aiesec", "total_mr"), "df MR per PIC")
    # Bar horizontal digambar dari bawah ke atas, jadi datanya dibalik
    # supaya PIC dengan MR terbanyak muncul di paling atas.
    data = df_by_pic.iloc[::-1]
    top_value = float(df_by_pic["total_mr"].max())

    fig = go.Figure(
        go.Bar(
            x=data["total_mr"],
            y=data["pic_aiesec"],
            orientation="h",
            # Batang teratas diberi warna penuh, sisanya sedikit lebih muda:
            # urutan tetap terbaca walau warnanya diabaikan.
            marker_color=[
                PRIMARY if value == top_value else PRIMARY_SOFT
                for value in data["total_mr"]
            ],
            text=data["total_mr"],
            textposition="outside",
            textfont={"size": LABEL_FONT_SIZE},
            cliponaxis=False,
            hovertemplate="%{y}: %{x} MR<extra></extra>",
            name="MR",
        )
    )
    fig.update_layout(showlegend=False, bargap=0.28)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(title="", gridcolor="rgba(0,0,0,0)")
    if description is None:
        description = (
            f"{len(df_by_pic)} PIC Â· total {int(df_by_pic['total_mr'].sum())} MR"
        )
    return _style(fig, title, description, height=max(200, 24 * len(data) + 70))


def mr_monthly_bar(
    df_by_month: pd.DataFrame,
    title: str | None = "Market Research per bulan",
    description: str | None = None,
) -> go.Figure:
    """Bar MR per bulan, urut masa jabatan (February -> January)."""
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title)

    _require_columns(df_by_month, ("month", "total_mr"), "df MR per bulan")
    values = df_by_month["total_mr"]

    fig = go.Figure(
        go.Bar(
            x=_month_labels(df_by_month["month"]),
            y=values,
            # Bulan tanpa MR dibuat abu: tetap terlihat sebagai bulan yang
            # ada, tapi tidak menarik perhatian.
            marker_color=[PRIMARY if value else COLOR_MUTED for value in values],
            text=[value if value else "" for value in values],
            textposition="outside",
            textfont={"size": LABEL_FONT_SIZE},
            cliponaxis=False,
            hovertemplate="%{x}: %{y} MR<extra></extra>",
            name="MR",
        )
    )
    fig.update_layout(showlegend=False, bargap=0.3)
    fig.update_yaxes(visible=False)
    if description is None:
        description = f"Total {int(values.sum())} MR Â· urut masa jabatan"
    return _style(fig, title, description)


def mr_monthly_area(
    df_by_month: pd.DataFrame,
    title: str | None = "Statistik Market Research",
    description: str | None = None,
    height: int = 335,
) -> go.Figure:
    """Area chart tren MR per bulan â€” chart utama di halaman Ringkasan.

    Bentuknya mengikuti arah desain yang diminta: garis melengkung dengan
    gradien lembut, penanda titik, dan bulan puncak diberi label supaya
    pembaca langsung tahu di mana titik tertingginya.
    """
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title, height=height)

    _require_columns(df_by_month, ("month", "total_mr"), "df MR per bulan")
    labels = _month_labels(df_by_month["month"])
    values = list(df_by_month["total_mr"])

    # Bulan setelah data terakhir dikosongkan (None), bukan digambar 0.
    # Kalau digambar 0, garisnya jatuh ke dasar dan terbaca sebagai
    # "MR-nya anjlok", padahal bulannya memang belum terjadi.
    last_filled = max(
        (index for index, value in enumerate(values) if value), default=-1
    )
    plotted = [
        value if index <= last_filled else None
        for index, value in enumerate(values)
    ]

    fig = go.Figure(
        go.Scatter(
            x=labels,
            y=plotted,
            mode="lines+markers",
            line={"color": PRIMARY, "width": 3, "shape": "spline", "smoothing": 0.6},
            marker={
                "size": 7, "color": "#FFFFFF",
                "line": {"color": PRIMARY, "width": 2.5},
            },
            fill="tozeroy",
            fillcolor=PRIMARY_FILL,
            connectgaps=False,
            hovertemplate="%{x}: %{y} MR<extra></extra>",
            name="MR",
        )
    )

    # Label pada bulan puncak: satu angka penting yang langsung terbaca,
    # tanpa perlu menempeli seluruh titik dengan teks.
    if any(values):
        peak_index = int(pd.Series(values).idxmax())
        fig.add_annotation(
            x=labels[peak_index], y=values[peak_index],
            text=f"<b>{values[peak_index]}</b> MR",
            showarrow=False, yshift=22,
            bgcolor=PRIMARY, bordercolor=PRIMARY, borderpad=5,
            font={"family": FONT_HEADING, "size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
        )

    fig.update_layout(showlegend=False)
    fig.update_yaxes(visible=False)
    fig.update_xaxes(tickfont={"size": LABEL_FONT_SIZE + 1})
    if description is None:
        total = int(sum(values))
        description = f"Total {total} MR sepanjang periode"
    return _style(fig, title, description, height=height)


def gauge_donut(
    value: float,
    total: float,
    label: str,
    title: str | None = None,
    on_dark: bool = False,
    height: int = 210,
) -> go.Figure:
    """Cincin progres dengan angka besar di tengah.

    Dipakai untuk Active Partners: porsi partner aktif dari seluruh
    portofolio. Angka dan keterangan ditulis di tengah, jadi cincinnya
    hanya penguat visual â€” bukan satu-satunya pembawa informasi.
    """
    if total is None or pd.isna(total) or float(total) <= 0:
        return _empty_figure(title, height=height)

    value = 0.0 if value is None or pd.isna(value) else float(value)
    total = float(total)
    remainder = max(total - value, 0.0)
    share = value / total * 100 if total else 0.0

    ring = "#FFFFFF" if on_dark else PRIMARY
    rest = "rgba(255,255,255,0.22)" if on_dark else COLOR_MUTED
    text_color = "#FFFFFF" if on_dark else COLOR_TEXT
    sub_color = "rgba(255,255,255,0.72)" if on_dark else COLOR_SUBTEXT

    fig = go.Figure(
        go.Pie(
            values=[value, remainder],
            labels=[label, "sisanya"],
            hole=0.78,
            sort=False,
            direction="clockwise",
            rotation=0,
            marker={"colors": [ring, rest], "line": {"width": 0}},
            textinfo="none",
            hovertemplate="%{label}: %{value:.0f}<extra></extra>",
        )
    )
    fig.add_annotation(
        text=(
            f"<span style='font-size:30px'><b>{value:.0f}</b></span>"
            f"<span style='font-size:13px;color:{sub_color}'>/{total:.0f}</span>"
            f"<br><span style='font-size:11px;color:{sub_color}'>{label}</span>"
        ),
        x=0.5, y=0.5, showarrow=False,
        font={"family": FONT_HEADING, "color": text_color},
    )
    fig.update_layout(showlegend=False, margin={"l": 6, "r": 6, "t": 6, "b": 6})
    styled = _style(fig, title, height=height)
    styled.update_layout(meta={"description": f"{label}: {value:.0f} dari {total:.0f} "
                                              f"({share:.1f}%)"})
    return styled


# ---------------------------------------------------------------------------
# Portofolio partner
# ---------------------------------------------------------------------------


def stakeholder_donut(
    df_stakeholder: pd.DataFrame,
    title: str | None = "Distribusi stakeholder",
    description: str | None = None,
) -> go.Figure:
    """Donut distribusi partner per stakeholder grouping.

    Args:
        df_stakeholder: hasil metrics.get_stakeholder_distribution().
    """
    if df_stakeholder is None or df_stakeholder.empty:
        return _empty_figure(title)

    _require_columns(df_stakeholder, ("stakeholder", "partner_count"), "df stakeholder")
    total = int(df_stakeholder["partner_count"].sum())

    fig = go.Figure(
        go.Pie(
            labels=df_stakeholder["stakeholder"],
            values=df_stakeholder["partner_count"],
            hole=0.62,
            sort=False,
            marker={
                "colors": list(PALETTE[: len(df_stakeholder)]),
                "line": {"color": "#FFFFFF", "width": 2},
            },
            # Angka menempel di potongannya; nama lengkap ada di legenda
            # dan hover, jadi warna bukan satu-satunya penanda.
            textinfo="value",
            textposition="inside",
            insidetextfont={"size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
            hovertemplate="%{label}: %{value} partner (%{percent})<extra></extra>",
        )
    )
    fig.add_annotation(
        text=f"<b>{total}</b><br><span style='font-size:10px'>partner</span>",
        x=0.5, y=0.5, showarrow=False,
        font={"size": BASE_FONT_SIZE + 6, "color": COLOR_TEXT},
    )
    fig.update_layout(
        showlegend=True,
        legend={
            "orientation": "v", "x": 1.0, "xanchor": "left",
            "y": 0.5, "yanchor": "middle",
            "font": {"size": LABEL_FONT_SIZE},
        },
        margin={"r": 150},
    )
    if description is None:
        description = f"{len(df_stakeholder)} kelompok stakeholder"
    return _style(fig, title, description)


# ---------------------------------------------------------------------------
# Sales funnel
# ---------------------------------------------------------------------------


def conversion_funnel_chart(
    df_funnel: pd.DataFrame,
    title: str | None = "Sales funnel conversion rate",
    description: str | None = None,
) -> go.Figure:
    """Funnel tahap penjualan. Nilainya PERSEN dari sheet, bukan hitungan ulang.

    Dataframe kosong di sini punya arti khusus: metrics.get_conversion_funnel()
    hanya mengembalikan frame kosong kalau sheet tidak punya angka untuk scope
    yang diminta (mis. periode gabungan beberapa bulan). Karena itu pesannya
    dibuat spesifik, bukan "tidak ada data" yang bisa disalahartikan sebagai
    "conversion rate-nya nol".
    """
    unavailable = "Sheet tidak menyediakan conversion rate untuk periode ini"
    if df_funnel is None or df_funnel.empty:
        return _empty_figure(title, unavailable)

    _require_columns(df_funnel, ("stage", "conversion_rate"), "df funnel")
    data = df_funnel.dropna(subset=["conversion_rate"])
    if data.empty:
        return _empty_figure(title, unavailable)

    # "2. proposal created & sent" -> "Proposal Created & Sent": nomor tahap
    # sudah tergambar oleh urutan funnel-nya.
    labels = [
        str(stage).split(". ", 1)[-1].title() if ". " in str(stage) else str(stage).title()
        for stage in data["stage"]
    ]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=data["conversion_rate"],
            marker={
                "color": [PRIMARY] * (len(labels) - 1) + [COLOR_POSITIVE],
                "line": {"color": "#FFFFFF", "width": 1},
            },
            connector={"line": {"color": COLOR_GRID, "width": 1}},
            textinfo="text",
            text=[_percent(value) for value in data["conversion_rate"]],
            textfont={"size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
            hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    if description is None:
        description = "Diambil apa adanya dari National 1.1"
    return _style(fig, title, description, height=max(240, 30 * len(labels) + 80))


def conversion_monthly_line(
    df_by_month: pd.DataFrame,
    title: str | None = "Conversion rate per bulan",
    description: str | None = None,
) -> go.Figure:
    """Garis conversion rate tahap Contract Signed per bulan.

    Bulan yang tidak punya angka di sheet dibiarkan bolong (bukan 0),
    supaya "belum ada data" tidak terbaca sebagai "gagal total".
    """
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title)

    _require_columns(df_by_month, ("month", "conversion_rate"), "df conversion per bulan")

    fig = go.Figure(
        go.Scatter(
            x=_month_labels(df_by_month["month"]),
            y=df_by_month["conversion_rate"],
            mode="lines+markers+text",
            line={"color": PRIMARY, "width": 2.5, "shape": "spline",
                  "smoothing": 0.4},
            marker={"size": 8, "line": {"color": "#FFFFFF", "width": 2}},
            fill="tozeroy",
            fillcolor=PRIMARY_FILL,
            text=[_percent(value) for value in df_by_month["conversion_rate"]],
            textposition="top center",
            textfont={"size": LABEL_FONT_SIZE, "color": COLOR_SUBTEXT},
            connectgaps=False,
            hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
            name="Contract signed",
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_yaxes(range=[0, 118], ticksuffix="%", dtick=25)
    if description is None:
        description = "Titik bolong = sheet belum punya angkanya"
    return _style(fig, title, description)


# ---------------------------------------------------------------------------
# Revenue â€” Financial dan In-Kind SELALU TERPISAH
# ---------------------------------------------------------------------------


def revenue_monthly_bar(
    df_by_month: pd.DataFrame,
    title: str | None,
    color: str = COLOR_FINANCIAL,
    value_label: str = "Nilai",
    description: str | None = None,
) -> go.Figure:
    """Bar nilai per bulan untuk SATU dataset revenue.

    Dipakai dua kali secara terpisah (financial, in-kind). Fungsi ini
    tidak pernah menerima dua dataset sekaligus.
    """
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title)

    _require_columns(df_by_month, ("month", "amount"), "df revenue per bulan")
    amounts = df_by_month["amount"]

    fig = go.Figure(
        go.Bar(
            x=_month_labels(df_by_month["month"]),
            y=amounts,
            marker_color=[color if value else COLOR_MUTED for value in amounts],
            text=[rupiah_compact(value) if value else "" for value in amounts],
            textposition="outside",
            textfont={"size": LABEL_FONT_SIZE},
            cliponaxis=False,
            customdata=[rupiah(value) for value in amounts],
            hovertemplate="%{x}: %{customdata}<extra></extra>",
            name=value_label,
        )
    )
    fig.update_layout(showlegend=False, bargap=0.3)
    # Sumbu nilai tidak ditampilkan: setiap batang sudah memuat angkanya.
    fig.update_yaxes(visible=False)
    if description is None:
        description = f"Total periode ini: {rupiah(float(amounts.sum()))}"
    return _style(fig, title, description)


def revenue_comparison_bar(
    financial_total: float,
    inkind_total: float,
    title: str | None = "Financial Revenue vs In-Kind Value",
    description: str | None = None,
    height: int = 205,
) -> go.Figure:
    """Dua batang berdampingan: financial dan in-kind.

    barmode='group' dipakai secara sengaja. Bar bertumpuk akan membaca
    tinggi totalnya sebagai penjumlahan kedua angka, dan aturan bisnis
    melarang keduanya dijumlahkan.
    """
    values = [financial_total, inkind_total]
    if all(value is None or pd.isna(value) for value in values):
        return _empty_figure(title, height=height)

    labels = ["Financial<br>Revenue", "In-Kind<br>Value"]
    fig = go.Figure()
    for label, value, color in zip(labels, values, (COLOR_FINANCIAL, COLOR_INKIND)):
        clean = 0.0 if value is None or pd.isna(value) else float(value)
        fig.add_trace(
            go.Bar(
                x=[label],
                y=[clean],
                name=label.replace("<br>", " "),
                marker_color=color,
                width=0.45,
                text=[rupiah_compact(value)],
                textposition="outside",
                textfont={"size": BASE_FONT_SIZE + 1},
                cliponaxis=False,
                customdata=[rupiah(value)],
                hovertemplate="%{x}: %{customdata}<extra></extra>",
            )
        )
    fig.update_layout(barmode="group", showlegend=False)
    fig.update_yaxes(visible=False)
    if description is None:
        description = "Dua KPI terpisah â€” tidak pernah dijumlahkan"
    return _style(fig, title, description, height=height)


def revenue_by_partner_bar(
    df_by_partner: pd.DataFrame,
    title: str | None,
    color: str = COLOR_FINANCIAL,
    top_n: int = 8,
    description: str | None = None,
) -> go.Figure:
    """Bar horizontal kontribusi per partner untuk satu dataset revenue."""
    if df_by_partner is None or df_by_partner.empty:
        return _empty_figure(title)

    _require_columns(df_by_partner, ("partner_name", "amount"), "df revenue per partner")
    data = df_by_partner.head(top_n).iloc[::-1]

    fig = go.Figure(
        go.Bar(
            x=data["amount"],
            y=data["partner_name"],
            orientation="h",
            marker_color=color,
            text=[rupiah_compact(value) for value in data["amount"]],
            textposition="outside",
            textfont={"size": LABEL_FONT_SIZE},
            cliponaxis=False,
            customdata=[rupiah(value) for value in data["amount"]],
            hovertemplate="%{y}: %{customdata}<extra></extra>",
            name="Nilai",
        )
    )
    fig.update_layout(showlegend=False, bargap=0.3)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(title="", gridcolor="rgba(0,0,0,0)")
    if description is None:
        shown = min(top_n, len(df_by_partner))
        description = f"{shown} dari {len(df_by_partner)} partner, urut terbesar"
    return _style(fig, title, description, height=max(200, 26 * len(data) + 70))


# ---------------------------------------------------------------------------
# Dokumen
# ---------------------------------------------------------------------------


def document_completeness_bar(
    df_completeness: pd.DataFrame,
    title: str | None = "Kelengkapan dokumen partner aktif",
    description: str | None = None,
) -> go.Figure:
    """Bar ADA vs belum ada, per jenis dokumen.

    Ini satu-satunya tempat barmode='stack' dibolehkan bersama grafik
    kontrak per bulan: ADA + belum ada memang berjumlah tepat sebanyak
    partner aktif, jadi tinggi totalnya punya arti.
    """
    if df_completeness is None or df_completeness.empty:
        return _empty_figure(title)

    _require_columns(
        df_completeness, ("document", "available", "missing"), "df kelengkapan dokumen"
    )
    labels = [str(value).upper() for value in df_completeness["document"]]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=labels, y=df_completeness["available"], name="Ada",
            marker_color=COLOR_POSITIVE,
            text=df_completeness["available"], textposition="inside",
            textfont={"size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
            hovertemplate="%{x} ada: %{y} partner<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=labels, y=df_completeness["missing"], name="Belum ada",
            marker_color=COLOR_MUTED,
            text=[value if value else "" for value in df_completeness["missing"]],
            textposition="inside",
            textfont={"size": LABEL_FONT_SIZE, "color": COLOR_TEXT},
            hovertemplate="%{x} belum ada: %{y} partner<extra></extra>",
        )
    )
    fig.update_layout(barmode="stack", showlegend=True, bargap=0.35)
    # Ruang ekstra di atas supaya angka di segmen teratas tidak terpotong
    # oleh legenda maupun tepi kartu.
    tallest = float((df_completeness["available"] + df_completeness["missing"]).max())
    fig.update_yaxes(visible=False, range=[0, tallest * 1.22])
    if description is None:
        description = "Hanya partner aktif"
    return _style(fig, title, description)


def document_tracker_heatmap(
    df_tracker: pd.DataFrame,
    document_fields: tuple[str, ...] = ("proposal", "mom", "loa", "invoice"),
    title: str | None = "Document tracker partner aktif",
    description: str | None = None,
) -> go.Figure:
    """Matriks partner x dokumen.

    Setiap sel diberi tanda teks (v / -), jadi isinya tetap terbaca tanpa
    membedakan warna.
    """
    if df_tracker is None or df_tracker.empty:
        return _empty_figure(title)

    columns = tuple(f"has_{document}" for document in document_fields)
    _require_columns(df_tracker, ("partner_name",) + columns, "df document tracker")

    matrix = df_tracker[list(columns)].astype(int).to_numpy()
    text = [["v" if cell else "-" for cell in row] for row in matrix]

    fig = go.Figure(
        go.Heatmap(
            z=matrix,
            x=[document.upper() for document in document_fields],
            y=list(df_tracker["partner_name"]),
            text=text,
            texttemplate="%{text}",
            textfont={"size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
            colorscale=[[0, COLOR_MUTED], [1, COLOR_POSITIVE]],
            showscale=False,
            xgap=4, ygap=4,
            hovertemplate="%{y} Â· %{x}: %{text}<extra></extra>",
        )
    )
    fig.update_xaxes(side="top", tickfont={"size": LABEL_FONT_SIZE})
    fig.update_yaxes(title="", autorange="reversed", gridcolor="rgba(0,0,0,0)")
    if description is None:
        description = f"{len(df_tracker)} partner aktif Â· v = dokumen ada"
    return _style(fig, title, description, height=max(240, 22 * len(df_tracker) + 90))


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def expiry_summary_bar(
    df_summary: pd.DataFrame,
    title: str | None = "Status masa kontrak partner",
    description: str | None = None,
) -> go.Figure:
    """Bar jumlah kontrak per kategori expiry, urut dari paling mendesak."""
    if df_summary is None or df_summary.empty:
        return _empty_figure(title)

    _require_columns(df_summary, ("expiry_category", "partner_count"), "df expiry")

    colors = [
        EXPIRY_COLORS.get(str(category), COLOR_MUTED)
        for category in df_summary["expiry_category"]
    ]
    # Label dipendekkan supaya lima kategori berdiri tegak tanpa saling
    # tindih; nama panjangnya tetap muncul di hover.
    short_labels = {
        EXPIRY_CATEGORIES[0]: "Expired",
        EXPIRY_CATEGORIES[1]: "Bln ini",
        EXPIRY_CATEGORIES[2]: "1â€“3 bln",
        EXPIRY_CATEGORIES[3]: ">3 bln",
        EXPIRY_CATEGORIES[4]: "Tanpa data",
    }
    labels = [
        short_labels.get(str(value), str(value)) for value in df_summary["expiry_category"]
    ]
    counts = list(df_summary["partner_count"])
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=counts,
            marker_color=colors,
            text=counts,
            textposition="outside",
            textfont={"size": LABEL_FONT_SIZE},
            cliponaxis=False,
            customdata=list(df_summary["expiry_category"]),
            hovertemplate="%{customdata}: %{y} partner<extra></extra>",
            name="Partner",
        )
    )
    fig.update_layout(showlegend=False, bargap=0.35)
    fig.update_xaxes(tickangle=0)
    fig.update_yaxes(visible=False, range=[0, (max(counts) if counts else 1) * 1.25])
    if description is None:
        description = "Kategori berbasis bulan (sumber tidak punya level harian)"
    return _style(fig, title, description)


def expiry_timeline_bar(
    df_expiry: pd.DataFrame,
    title: str | None = "Kontrak menurut bulan berakhir",
    active_only: bool = True,
    description: str | None = None,
) -> go.Figure:
    """Jumlah kontrak yang berakhir per bulan, urut waktu.

    Args:
        df_expiry: hasil metrics.get_partnership_expiry().
        active_only: True untuk menghitung hanya partner aktif.
    """
    if df_expiry is None or df_expiry.empty:
        return _empty_figure(title)

    _require_columns(
        df_expiry, ("end_month", "expiry_category", "is_active"), "df expiry"
    )
    data = df_expiry[df_expiry["is_active"]] if active_only else df_expiry
    data = data[data["end_month"].notna()]
    if data.empty:
        return _empty_figure(title, "Tidak ada kontrak dengan Month End terisi")

    grouped = (
        data.groupby([data["end_month"].astype(str), "expiry_category"], observed=True)
        .size()
        .rename("partner_count")
        .reset_index()
    )
    month_column = grouped.columns[0]
    grouped = grouped.sort_values(month_column)

    fig = go.Figure()
    for category in EXPIRY_CATEGORIES:
        subset = grouped[grouped["expiry_category"] == category]
        if subset.empty:
            continue
        fig.add_trace(
            go.Bar(
                x=subset[month_column],
                y=subset["partner_count"],
                name=str(category).title(),
                marker_color=EXPIRY_COLORS.get(category, COLOR_MUTED),
                text=subset["partner_count"],
                textposition="inside",
                textfont={"size": LABEL_FONT_SIZE, "color": "#FFFFFF"},
                hovertemplate="%{x}: %{y} partner (" + str(category) + ")<extra></extra>",
            )
        )
    # Bertumpuk boleh di sini: tinggi total = jumlah kontrak yang berakhir
    # pada bulan itu, jadi angkanya memang bermakna.
    fig.update_layout(barmode="stack", showlegend=True, bargap=0.35)
    fig.update_yaxes(visible=False)
    if description is None:
        scope = "partner aktif" if active_only else "seluruh partner"
        description = f"Menampilkan {scope}"
    return _style(fig, title, description)
