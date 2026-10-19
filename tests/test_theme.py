"""Light/Dark theme tests: selector state, stylesheet choice, chart palette.

Tampilan visual tidak bisa diuji otomatis, jadi yang diuji di sini adalah hal
yang BISA diverifikasi: tema mana yang aktif, lembar gaya mana yang dipasang,
palet grafik mana yang dipakai, dan bahwa mengganti tema tidak menyentuh data.
"""
import ast
from pathlib import Path
import re
import unittest

from src import charts

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
APP_SOURCE = APP_PATH.read_text(encoding="utf-8")
CONFIG_PATH = APP_PATH.parent / ".streamlit" / "config.toml"


def _targets(node) -> set[str]:
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def app_constant(name: str):
    """Ambil konstanta sederhana dari app.py tanpa menjalankan Streamlit."""
    tree = ast.parse(APP_SOURCE)
    for node in tree.body:
        if name in _targets(node) and getattr(node, "value", None) is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} tidak ditemukan di app.py")


def theme_namespace(state: dict | None = None) -> dict:
    """Jalankan konstanta tema + selected_theme() di namespace terisolasi."""
    tree = ast.parse(APP_SOURCE)
    wanted = {"THEME_DARK", "THEME_LIGHT", "THEME_CHOICES", "THEME_STATE_KEY"}
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "selected_theme")
        or (_targets(node) & wanted)
    ]

    class FakeSessionState(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    namespace = {
        "st": type("st", (), {"session_state": FakeSessionState(state or {})})
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_PATH), "exec"), namespace)
    return namespace


def selected_theme(state: dict) -> str:
    """Jalankan selected_theme() dari app.py terhadap session state tiruan."""
    return theme_namespace(state)["selected_theme"]()


class ThemeSelectionTests(unittest.TestCase):
    def test_dark_is_the_default_theme(self):
        self.assertEqual(app_constant("THEME_DARK"), "Dark")
        self.assertEqual(theme_namespace()["THEME_CHOICES"], ("Dark", "Light"))
        self.assertEqual(selected_theme({}), "Dark")

    def test_session_state_selection_is_honored(self):
        key = app_constant("THEME_STATE_KEY")
        self.assertEqual(selected_theme({key: "Light"}), "Light")
        self.assertEqual(selected_theme({key: "Dark"}), "Dark")

    def test_unknown_or_empty_values_fall_back_to_dark(self):
        key = app_constant("THEME_STATE_KEY")
        for value in (None, "", "Solarized", 0):
            with self.subTest(value=value):
                self.assertEqual(selected_theme({key: value}), "Dark")

    def test_theme_is_read_before_the_stylesheets_are_injected(self):
        """Tanpa ini, halaman akan sempat tampil gelap sebelum berubah terang."""
        theme_read = APP_SOURCE.index("THEME = selected_theme()")
        first_injection = APP_SOURCE.index("def apply_entry_theme()")
        dashboard_injection = APP_SOURCE.index(
            "st.markdown(LIGHT_STYLE if IS_LIGHT_THEME else DARK_STYLE"
        )
        self.assertLess(theme_read, first_injection)
        self.assertLess(theme_read, dashboard_injection)

    def test_selector_lives_in_the_sidebar_and_cannot_be_deselected(self):
        sidebar = APP_SOURCE[APP_SOURCE.index("def sidebar_controls()"):]
        sidebar = sidebar[: sidebar.index("def header(")]
        self.assertIn('sidebar_group("Appearance")', sidebar)
        self.assertIn("st.radio(", sidebar)
        self.assertIn("key=THEME_STATE_KEY", sidebar)
        # Pemilih tema hanya menulis ke session state; tidak ada akses store.
        selector = sidebar[sidebar.index('sidebar_group("Appearance")'):]
        selector = selector[: selector.index('sidebar_group("Data Source")')]
        for forbidden in ("get_store", "load_frames", "set_invoice_exception", "replace_requirements"):
            self.assertNotIn(forbidden, selector)


class StylesheetTests(unittest.TestCase):
    def style_block(self, name: str) -> str:
        match = re.search(rf'^{name} = """(.*?)"""', APP_SOURCE, re.DOTALL | re.MULTILINE)
        self.assertIsNotNone(match, f"{name} tidak ditemukan")
        return match.group(1)

    def test_dark_style_is_unchanged_in_structure(self):
        dark = self.style_block("DARK_STYLE")
        # Warna inti Dark Mode yang sudah ada harus tetap ada.
        for token in ("#0a1629", "#2b4263", "#172a46", "stSidebar"):
            self.assertIn(token, dark)

    def test_light_style_uses_a_white_and_soft_blue_palette(self):
        light = self.style_block("LIGHT_STYLE")
        self.assertIn("#FFFFFF", light)       # kartu putih
        self.assertIn("#152945", light)       # teks navy
        self.assertIn("#2563B6", light)       # aksen biru terkendali
        self.assertIn("#E8F1FD", light)       # aksen biru pucat
        self.assertIn("#DEE8F6", light)       # garis biru-abu halus
        # Tidak boleh ada latar navy gelap Dark Mode yang ikut terbawa.
        for dark_token in ("#0a1629", "#081221", "#172a46"):
            self.assertNotIn(dark_token, light)

    def test_light_style_covers_every_interactive_surface(self):
        light = self.style_block("LIGHT_STYLE")
        for hook in (
            'data-testid="stMetric"',
            'data-testid="stTab"',
            'data-testid="stExpander"',
            'data-testid="stButton"',
            'data-testid="stTextInputRootElement"',
            'data-baseweb="select"',
            'data-baseweb="popover"',
            'data-testid="stAlertContainer"',
            'data-testid="stDataFrame"',
            'data-testid="stProgress"',
            'data-testid="stSidebar"',
            'data-testid="stRadio"',
            ".bd-badge-missing",
            ".snd-status",
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, light)

    def test_both_themes_style_the_same_component_hooks(self):
        """Light Mode tidak boleh melewatkan komponen yang Dark Mode warnai."""
        dark = self.style_block("DARK_STYLE")
        light = self.style_block("LIGHT_STYLE")
        hooks = set(re.findall(r'(\.bd-[a-z-]+|\.snd-[a-z-]+)', dark))
        missing = sorted(hook for hook in hooks if hook not in light)
        self.assertEqual(missing, [], f"Light Mode belum mengatur: {missing}")

    def test_entry_page_has_both_variants(self):
        self.assertIn("ENTRY_DARK_STYLE", APP_SOURCE)
        self.assertIn("ENTRY_LIGHT_STYLE", APP_SOURCE)
        self.assertNotIn(
            "st.markdown(ENTRY_DARK_STYLE, unsafe_allow_html=True)", APP_SOURCE
        )
        self.assertEqual(APP_SOURCE.count("apply_entry_theme()"), 3)  # 1 def + 2 pemakaian


class ChartThemeTests(unittest.TestCase):
    def tearDown(self):
        charts.use_theme(charts.THEME_DARK)

    def test_dark_palette_is_the_default(self):
        self.assertEqual(charts.active_theme(), charts.THEME_DARK)
        self.assertEqual(charts.COLOR_TEXT, "#E7EFFF")
        self.assertEqual(charts.TEMPLATE, "plotly_dark")

    def test_light_palette_switches_text_grid_and_template(self):
        charts.use_theme(charts.THEME_LIGHT)
        self.assertEqual(charts.TEMPLATE, "plotly_white")
        self.assertEqual(charts.COLOR_TEXT, "#152945")
        self.assertEqual(charts.COLOR_SUBTEXT, "#5A7191")
        self.assertEqual(charts.COLOR_GRID, "#E4EBF7")

    def test_brand_colors_never_change_between_themes(self):
        before = (charts.PRIMARY, charts.SKY, charts.NAVY, charts.PALETTE,
                  charts.COLOR_POSITIVE, charts.AIESEC_BLUE)
        charts.use_theme(charts.THEME_LIGHT)
        after = (charts.PRIMARY, charts.SKY, charts.NAVY, charts.PALETTE,
                 charts.COLOR_POSITIVE, charts.AIESEC_BLUE)
        self.assertEqual(before, after)

    def test_labels_drawn_on_muted_bars_stay_white_in_both_themes(self):
        self.assertEqual(charts.COLOR_ON_MUTED, "#FFFFFF")
        charts.use_theme(charts.THEME_LIGHT)
        self.assertEqual(charts.COLOR_ON_MUTED, "#FFFFFF")

    def test_figures_follow_the_active_theme(self):
        import pandas as pd

        frame = pd.DataFrame({"month": ["october"], "total_mr": [3]})
        dark_figure = charts.mr_monthly_area(frame)
        self.assertEqual(dark_figure.layout.font.color, "#E7EFFF")
        charts.use_theme(charts.THEME_LIGHT)
        light_figure = charts.mr_monthly_area(frame)
        self.assertEqual(light_figure.layout.font.color, "#152945")

    def test_unknown_theme_is_rejected_instead_of_silently_ignored(self):
        with self.assertRaises(ValueError):
            charts.use_theme("neon")
        self.assertEqual(charts.active_theme(), charts.THEME_DARK)


class NativeThemeConfigTests(unittest.TestCase):
    def setUp(self):
        import tomllib

        self.config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_existing_dark_base_is_preserved(self):
        theme = self.config["theme"]
        self.assertEqual(theme["base"], "dark")
        self.assertEqual(theme["backgroundColor"], "#081222")
        self.assertEqual(theme["textColor"], "#E7EFFF")

    def test_light_palette_is_available_for_native_widgets(self):
        light = self.config["theme"]["light"]
        self.assertEqual(light["backgroundColor"], "#FBFDFF")
        self.assertEqual(light["textColor"], "#152945")
        self.assertEqual(light["primaryColor"], "#2563B6")


if __name__ == "__main__":
    unittest.main()
