"""
charts.py — VISUALIZATION LAYER (Phase 10)

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

4. Palet memakai Okabe-Ito (colorblind-safe) dengan biru AIESEC sebagai
   warna utama.

5. Dataframe kosong menghasilkan figure berisi keterangan "tidak ada
   data", bukan exception dan bukan grafik kosong tanpa penjelasan.

6. Kolom yang kurang menghasilkan KeyError dengan pesan jelas.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .metrics import EXPIRY_CATEGORIES

# ---------------------------------------------------------------------------
# Palet & gaya
# ---------------------------------------------------------------------------

AIESEC_BLUE = "#037EF3"

# Okabe-Ito: aman untuk deuteranopia/protanopia, kontras baik di latar putih.
PALETTE: tuple[str, ...] = (
    "#0072B2",  # biru
    "#E69F00",  # oranye
    "#009E73",  # hijau
    "#CC79A7",  # merah muda
    "#56B4E9",  # biru langit
    "#D55E00",  # vermilion
    "#8C6D31",  # cokelat
    "#666666",  # abu
)

COLOR_FINANCIAL = "#0072B2"
COLOR_INKIND = "#E69F00"
COLOR_MUTED = "#CCCCCC"
COLOR_TEXT = "#222222"

# Warna kategori expiry: makin mendesak makin panas.
EXPIRY_COLORS: dict[str, str] = {
    EXPIRY_CATEGORIES[0]: "#D55E00",  # expired
    EXPIRY_CATEGORIES[1]: "#E69F00",  # berakhir bulan ini
    EXPIRY_CATEGORIES[2]: "#56B4E9",  # 1-3 bulan lagi
    EXPIRY_CATEGORIES[3]: "#009E73",  # lebih dari 3 bulan
    EXPIRY_CATEGORIES[4]: "#666666",  # tanpa data
}

# Ukuran font minimum yang masih nyaman dibaca di layar proyektor rapat BD.
BASE_FONT_SIZE = 13

EMPTY_MESSAGE = "Tidak ada data untuk periode ini"


# ---------------------------------------------------------------------------
# Util internal
# ---------------------------------------------------------------------------


def _require_columns(df: pd.DataFrame, columns: tuple[str, ...], name: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise KeyError(f"{name} tidak punya kolom: {', '.join(missing)}")


def _style(fig: go.Figure, title: str, description: str = "") -> go.Figure:
    """Gaya dasar yang sama untuk semua figure.

    description dipakai sebagai keterangan yang terbaca pembaca layar
    maupun mata biasa, bukan hanya tooltip.
    """
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left"},
        template="plotly_white",
        font={"size": BASE_FONT_SIZE, "color": COLOR_TEXT},
        margin={"l": 70, "r": 30, "t": 70, "b": 60},
        showlegend=fig.layout.showlegend,
    )
    if description:
        fig.add_annotation(
            text=description,
            xref="paper", yref="paper",
            x=0, y=1.06,
            showarrow=False,
            font={"size": BASE_FONT_SIZE - 2, "color": "#555555"},
            align="left",
        )
        fig.update_layout(margin={"l": 70, "r": 30, "t": 95, "b": 60})
    fig.update_layout(meta={"description": description or title})
    return fig


def _empty_figure(title: str, message: str = EMPTY_MESSAGE) -> go.Figure:
    """Figure pengganti saat tidak ada data. Menjelaskan diri sendiri."""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper",
        x=0.5, y=0.5,
        showarrow=False,
        font={"size": BASE_FONT_SIZE + 1, "color": "#555555"},
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.update_layout(showlegend=False)
    return _style(fig, title)


def rupiah(value: float) -> str:
    """Format angka uang gaya Indonesia: 26750000 -> Rp26.750.000."""
    if value is None or pd.isna(value):
        return "-"
    return "Rp" + f"{float(value):,.0f}".replace(",", ".")


def _percent(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.2f}%"


def _month_labels(values) -> list[str]:
    return [str(value).title() for value in values]


# ---------------------------------------------------------------------------
# Market Research
# ---------------------------------------------------------------------------


def mr_by_pic_bar(df_by_pic: pd.DataFrame, title: str = "Market Research per PIC") -> go.Figure:
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

    fig = go.Figure(
        go.Bar(
            x=data["total_mr"],
            y=data["pic_aiesec"],
            orientation="h",
            marker_color=AIESEC_BLUE,
            text=data["total_mr"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x} MR<extra></extra>",
            name="MR",
        )
    )
    fig.update_layout(showlegend=False, height=max(320, 34 * len(data) + 120))
    fig.update_xaxes(title="Jumlah MR")
    fig.update_yaxes(title="")
    return _style(
        fig, title,
        f"{len(df_by_pic)} PIC, total {int(df_by_pic['total_mr'].sum())} MR. "
        "Satu MR = satu kemunculan nama PIC.",
    )


def mr_monthly_bar(
    df_by_month: pd.DataFrame, title: str = "Market Research per bulan"
) -> go.Figure:
    """Bar MR per bulan, urut masa jabatan (February -> January)."""
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title)

    _require_columns(df_by_month, ("month", "total_mr"), "df MR per bulan")

    fig = go.Figure(
        go.Bar(
            x=_month_labels(df_by_month["month"]),
            y=df_by_month["total_mr"],
            marker_color=AIESEC_BLUE,
            text=df_by_month["total_mr"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}: %{y} MR<extra></extra>",
            name="MR",
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title="Bulan masa jabatan")
    fig.update_yaxes(title="Jumlah MR")
    return _style(
        fig, title,
        "Urutan bulan mengikuti masa jabatan. February & March tidak ada di "
        "data MR karena dibuang di Phase 3.",
    )


# ---------------------------------------------------------------------------
# Portofolio partner
# ---------------------------------------------------------------------------


def stakeholder_donut(
    df_stakeholder: pd.DataFrame, title: str = "Distribusi stakeholder"
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
            hole=0.55,
            sort=False,
            marker={"colors": list(PALETTE[: len(df_stakeholder)])},
            # Label ikut di tiap potongan: warna bukan satu-satunya penanda.
            textinfo="label+value",
            texttemplate="%{label}<br>%{value} (%{percent})",
            hovertemplate="%{label}: %{value} partner (%{percent})<extra></extra>",
        )
    )
    fig.add_annotation(
        text=f"<b>{total}</b><br>partner",
        x=0.5, y=0.5, showarrow=False,
        font={"size": BASE_FONT_SIZE + 3},
    )
    fig.update_layout(showlegend=True, legend={"orientation": "v"})
    return _style(fig, title, f"Total {total} partner.")


# ---------------------------------------------------------------------------
# Sales funnel
# ---------------------------------------------------------------------------


def conversion_funnel_chart(
    df_funnel: pd.DataFrame, title: str = "Sales funnel conversion rate"
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

    labels = [str(stage).title() for stage in data["stage"]]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=data["conversion_rate"],
            marker={"color": AIESEC_BLUE},
            textinfo="text",
            text=[_percent(value) for value in data["conversion_rate"]],
            hovertemplate="%{y}: %{x:.2f}%<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False)
    return _style(
        fig, title,
        "Angka diambil langsung dari National 1.1, tidak dihitung ulang.",
    )


def conversion_monthly_line(
    df_by_month: pd.DataFrame, title: str = "Conversion rate per bulan"
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
            line={"color": AIESEC_BLUE, "width": 3},
            marker={"size": 9},
            text=[_percent(value) for value in df_by_month["conversion_rate"]],
            textposition="top center",
            connectgaps=False,
            hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
            name="Contract signed",
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title="Bulan masa jabatan")
    fig.update_yaxes(title="Conversion rate (%)", range=[0, 105])
    return _style(
        fig, title,
        "Titik yang bolong berarti sheet belum punya angkanya untuk bulan itu.",
    )


# ---------------------------------------------------------------------------
# Revenue — Financial dan In-Kind SELALU TERPISAH
# ---------------------------------------------------------------------------


def revenue_monthly_bar(
    df_by_month: pd.DataFrame,
    title: str,
    color: str = COLOR_FINANCIAL,
    value_label: str = "Nilai",
) -> go.Figure:
    """Bar nilai per bulan untuk SATU dataset revenue.

    Dipakai dua kali secara terpisah (financial, in-kind). Fungsi ini
    tidak pernah menerima dua dataset sekaligus.
    """
    if df_by_month is None or df_by_month.empty:
        return _empty_figure(title)

    _require_columns(df_by_month, ("month", "amount"), "df revenue per bulan")

    fig = go.Figure(
        go.Bar(
            x=_month_labels(df_by_month["month"]),
            y=df_by_month["amount"],
            marker_color=color,
            text=[rupiah(value) if value else "" for value in df_by_month["amount"]],
            textposition="outside",
            cliponaxis=False,
            customdata=df_by_month["records"] if "records" in df_by_month else None,
            hovertemplate="%{x}: %{text}<extra></extra>",
            name=value_label,
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title="Bulan masa jabatan")
    fig.update_yaxes(title=value_label, tickprefix="Rp", separatethousands=True)
    total = float(df_by_month["amount"].sum())
    return _style(fig, title, f"Total periode ini: {rupiah(total)}.")


def revenue_comparison_bar(
    financial_total: float,
    inkind_total: float,
    title: str = "Financial Revenue vs In-Kind Value",
) -> go.Figure:
    """Dua batang berdampingan: financial dan in-kind.

    barmode='group' dipakai secara sengaja. Bar bertumpuk akan membaca
    tinggi totalnya sebagai penjumlahan kedua angka, dan aturan bisnis
    melarang keduanya dijumlahkan.
    """
    values = [financial_total, inkind_total]
    if all(value is None or pd.isna(value) for value in values):
        return _empty_figure(title)

    labels = ["Financial Revenue", "In-Kind Value"]
    fig = go.Figure()
    for label, value, color in zip(labels, values, (COLOR_FINANCIAL, COLOR_INKIND)):
        fig.add_trace(
            go.Bar(
                x=[label],
                y=[0.0 if value is None or pd.isna(value) else float(value)],
                name=label,
                marker_color=color,
                text=[rupiah(value)],
                textposition="outside",
                cliponaxis=False,
                hovertemplate=f"{label}: %{{text}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="group", showlegend=False)
    fig.update_yaxes(title="Nilai", tickprefix="Rp", separatethousands=True)
    return _style(
        fig, title,
        "Dua KPI terpisah. Keduanya tidak pernah dijumlahkan menjadi satu angka.",
    )


def revenue_by_partner_bar(
    df_by_partner: pd.DataFrame,
    title: str,
    color: str = COLOR_FINANCIAL,
    top_n: int = 10,
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
            text=[rupiah(value) for value in data["amount"]],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{text}<extra></extra>",
            name="Nilai",
        )
    )
    fig.update_layout(showlegend=False, height=max(320, 34 * len(data) + 120))
    fig.update_xaxes(title="Nilai", tickprefix="Rp", separatethousands=True)
    fig.update_yaxes(title="")
    shown = min(top_n, len(df_by_partner))
    return _style(
        fig, title,
        f"Menampilkan {shown} dari {len(df_by_partner)} partner, urut terbesar.",
    )


# ---------------------------------------------------------------------------
# Dokumen
# ---------------------------------------------------------------------------


def document_completeness_bar(
    df_completeness: pd.DataFrame, title: str = "Kelengkapan dokumen partner aktif"
) -> go.Figure:
    """Bar ADA vs belum ada, per jenis dokumen.

    Ini satu-satunya tempat barmode='stack' dibolehkan: ADA + belum ada
    memang berjumlah tepat sebanyak partner aktif, jadi tinggi totalnya
    punya arti.
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
            marker_color="#009E73",
            text=df_completeness["available"], textposition="inside",
            hovertemplate="%{x} ada: %{y} partner<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=labels, y=df_completeness["missing"], name="Belum ada",
            marker_color=COLOR_MUTED,
            text=df_completeness["missing"], textposition="inside",
            hovertemplate="%{x} belum ada: %{y} partner<extra></extra>",
        )
    )
    fig.update_layout(barmode="stack", showlegend=True)
    fig.update_xaxes(title="Jenis dokumen")
    fig.update_yaxes(title="Jumlah partner aktif")
    return _style(
        fig, title,
        "Hanya partner aktif. Dokumen partner yang kontraknya sudah berakhir "
        "tidak perlu ditagih lagi.",
    )


def document_tracker_heatmap(
    df_tracker: pd.DataFrame,
    document_fields: tuple[str, ...] = ("proposal", "mom", "loa", "invoice"),
    title: str = "Document tracker partner aktif",
) -> go.Figure:
    """Matriks partner x dokumen. Setiap sel diberi teks ADA / belum,
    jadi isinya tetap terbaca tanpa membedakan warna."""
    if df_tracker is None or df_tracker.empty:
        return _empty_figure(title)

    columns = tuple(f"has_{document}" for document in document_fields)
    _require_columns(df_tracker, ("partner_name",) + columns, "df document tracker")

    matrix = df_tracker[list(columns)].astype(int).to_numpy()
    text = [["ADA" if cell else "belum" for cell in row] for row in matrix]

    fig = go.Figure(
        go.Heatmap(
            z=matrix,
            x=[document.upper() for document in document_fields],
            y=list(df_tracker["partner_name"]),
            text=text,
            texttemplate="%{text}",
            colorscale=[[0, COLOR_MUTED], [1, "#009E73"]],
            showscale=False,
            xgap=3, ygap=3,
            hovertemplate="%{y} - %{x}: %{text}<extra></extra>",
        )
    )
    fig.update_layout(height=max(320, 26 * len(df_tracker) + 140))
    fig.update_xaxes(title="", side="top")
    fig.update_yaxes(title="", autorange="reversed")
    return _style(
        fig, title,
        f"{len(df_tracker)} partner aktif, urut dari yang dokumennya paling "
        "banyak belum ada.",
    )


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def expiry_summary_bar(
    df_summary: pd.DataFrame, title: str = "Status masa kontrak partner"
) -> go.Figure:
    """Bar jumlah kontrak per kategori expiry, urut dari paling mendesak."""
    if df_summary is None or df_summary.empty:
        return _empty_figure(title)

    _require_columns(df_summary, ("expiry_category", "partner_count"), "df expiry")

    colors = [
        EXPIRY_COLORS.get(str(category), COLOR_MUTED)
        for category in df_summary["expiry_category"]
    ]
    fig = go.Figure(
        go.Bar(
            x=[str(value).title() for value in df_summary["expiry_category"]],
            y=df_summary["partner_count"],
            marker_color=colors,
            text=df_summary["partner_count"],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}: %{y} partner<extra></extra>",
            name="Partner",
        )
    )
    fig.update_layout(showlegend=False)
    fig.update_xaxes(title="Kategori")
    fig.update_yaxes(title="Jumlah partner")
    return _style(
        fig, title,
        "Kategori berbasis bulan, karena Month End di sumber hanya level bulan.",
    )


def expiry_timeline_bar(
    df_expiry: pd.DataFrame,
    title: str = "Kontrak menurut bulan berakhir",
    active_only: bool = True,
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
        .rename(columns={"level_0": "end_month"})
    )
    grouped = grouped.sort_values(grouped.columns[0])
    month_column = grouped.columns[0]

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
                hovertemplate="%{x}: %{y} partner (" + str(category) + ")<extra></extra>",
            )
        )
    # Bertumpuk boleh di sini: tinggi total = jumlah kontrak yang berakhir
    # pada bulan itu, jadi angkanya memang bermakna.
    fig.update_layout(barmode="stack", showlegend=True)
    fig.update_xaxes(title="Bulan berakhir kontrak")
    fig.update_yaxes(title="Jumlah kontrak")
    scope = "partner aktif" if active_only else "seluruh partner"
    return _style(fig, title, f"Menampilkan {scope}.")
