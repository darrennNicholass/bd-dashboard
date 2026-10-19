"""Market Research cutover tests: source selection, schema mapping, diagnostics.

Semua akses Google Sheets dipalsukan (grid mentah disuntikkan langsung), jadi
tes ini tidak pernah menyentuh jaringan maupun kredensial.
"""
import unittest
from unittest import mock

import pandas as pd

from src import metrics, mr_sources, periods, preparation, sheets

TODAY = pd.Timestamp("2026-11-20")  # masa jabatan 2026: FEB 26 ... JAN 27


def active_grid(rows):
    """Satu tab Market Research Active ESSM dengan baris penanda bulan."""
    grid = [["NO", "PARTNER'S NAME", "PIC FROM AIESEC", "DATE OF MR"]]
    grid.extend(rows)
    return grid


ACTIVE_GRIDS = [
    (
        "[ELDs] Market Research",
        active_grid([
            ["🌸april", "", "", ""],
            ["1", "April Partner", "Bima", "2026-04-02"],
            ["august", "", "", ""],
            ["1", "August Partner", "Citra", "2026-08-11"],
            ["september", "", "", ""],
            ["1", "Sept Partner A", "Dina", "2026-09-03"],
            ["2", "Sept Partner B", "Eka", "2026-09-10"],
            ["october", "", "", ""],
            # Baris Oktober yang masih tertinggal di Active ESSM. Setelah
            # cutover ini TIDAK boleh ikut dihitung.
            ["1", "Legacy October", "Dina", "2026-10-02"],
        ]),
    ),
    (
        "[SS] Market Research",
        active_grid([
            ["september", "", "", ""],
            # Partner yang sama diteliti tim lain = aktivitas MR yang sah.
            ["1", "Sept Partner A", "Fajar", "2026-09-04"],
        ]),
    ),
]

DATABASE_GRIDS = [
    (
        "MR Oct 2026 onwards",
        [
            ["Market Research Database 26/27"],  # baris judul di atas header
            ["No", "Partner Name", "AIESEC PIC", "Date of MR", "Status"],
            ["1", "Gamma Ltd", "Gina", "2026-10-03", "Prospect"],
            ["2", "Delta Ltd", "Hadi", "2026-10-04", "Prospect"],
            ["2", "Delta Ltd", "Hadi", "2026-10-04", "Prospect"],  # duplikat identik
            ["3", "Epsilon Co", "Gina", "2026-11-05", "Prospect"],
            ["4", "Zeta Co", "", "2026-11-06", "Prospect"],        # tanpa PIC
            ["", "TOTAL", "", "", ""],                              # baris ringkasan
        ],
    ),
]


def load(**kwargs):
    return mr_sources.build_df_mr(
        today=TODAY, active_grids=ACTIVE_GRIDS, database_grids=DATABASE_GRIDS, **kwargs
    )


# Header asli Partnership Database BD 26.27. "PIC Name" = kontak dari pihak
# partner (spasi di belakangnya memang ada di tab ELDs), "AIESEC PIC" = member.
SECTION_HEADER = [
    "Partner Name", "PIC Name ", "Email", "Phone Number", "AIESEC PIC",
    "Proposal", "MoM", "LOA", "Partnership Booklet",
]


def sectioned_tab(title, sections, header=SECTION_HEADER):
    """Satu tab Partnership Database: judul, lalu bagian per bulan.

    Bentuknya sama dengan get_all_values(): judul gabungan A1:I2, baris kosong,
    lalu tiap bagian = penanda bulan (sel gabungan, hanya kolom pertama yang
    berisi) + header yang diulang + data. Tidak ada kolom bulan/tanggal.
    """
    width = len(header)

    def padded(values):
        return list(values) + [""] * (width - len(values))

    grid = [padded([f"PARTNERSHIP DATABASE {title}"]), padded([]), padded([])]
    for marker, rows in sections:
        grid.append(padded([marker]))
        grid.append(list(header))
        grid.extend(padded(row) for row in rows)
        grid.extend([padded([]), padded([])])
    return grid


def partner_row(partner, aiesec_pic, contact="Contact Person"):
    """Partner Name, PIC Name (kontak partner), Email, Phone Number, AIESEC PIC."""
    return [partner, contact, "", "", aiesec_pic]


SECTIONED_GRIDS = [
    ("ELDs", sectioned_tab("ELDs", [("OCTOBER", []), ("NOVEMBER", [])])),
    (
        "EwAs",
        sectioned_tab(
            "EwAs",
            [
                ("OCTOBER", [
                    partner_row("Partner Satu", "Awa"),
                    partner_row("Partner Dua", "Awa"),
                    partner_row("Partner Tiga", "Bima"),
                ]),
                ("NOVEMBER", [partner_row("Partner Empat", "Citra")]),
                ("DESEMBER", [partner_row("Partner Lima", "Awa")]),
                ("JANUARI", [partner_row("Partner Enam", "Bima")]),
                # FEBRUARI sesudah JANUARI = Februari 2027 (masa jabatan berikutnya).
                ("FEBRUARI", [partner_row("Partner Tujuh", "Citra")]),
            ],
        ),
    ),
    ("SS", sectioned_tab("SS", [("OCTOBER", []), ("NOVEMBER", [])])),
]


class SourceSelectionRuleTests(unittest.TestCase):
    def test_periods_before_october_2026_use_active_essm(self):
        for month in ("february", "april", "august", "september"):
            with self.subTest(month=month):
                self.assertEqual(
                    mr_sources.source_for_month(month, today=TODAY),
                    mr_sources.SOURCE_ACTIVE_ESSM,
                )

    def test_october_2026_and_later_use_the_new_database(self):
        for month in ("october", "november", "december", "january"):
            with self.subTest(month=month):
                self.assertEqual(
                    mr_sources.source_for_month(month, today=TODAY),
                    mr_sources.SOURCE_MR_DATABASE,
                )

    def test_rule_is_anchored_to_the_calendar_period_not_the_month_name(self):
        self.assertEqual(
            mr_sources.source_for_period(pd.Period("2026-09", freq="M")),
            mr_sources.SOURCE_ACTIVE_ESSM,
        )
        self.assertEqual(
            mr_sources.source_for_period(pd.Period("2026-10", freq="M")),
            mr_sources.SOURCE_MR_DATABASE,
        )
        self.assertEqual(
            mr_sources.source_for_period(pd.Timestamp("2026-10-01")),
            mr_sources.SOURCE_MR_DATABASE,
        )
        self.assertEqual(
            mr_sources.source_for_period(pd.Timestamp("2026-09-30")),
            mr_sources.SOURCE_ACTIVE_ESSM,
        )

    def test_selection_reports_every_source_it_needs(self):
        self.assertEqual(
            mr_sources.sources_for_selection("september", today=TODAY),
            (mr_sources.SOURCE_ACTIVE_ESSM,),
        )
        self.assertEqual(
            mr_sources.sources_for_selection("october", today=TODAY),
            (mr_sources.SOURCE_MR_DATABASE,),
        )
        self.assertEqual(
            mr_sources.sources_for_selection("all", today=TODAY),
            (mr_sources.SOURCE_ACTIVE_ESSM, mr_sources.SOURCE_MR_DATABASE),
        )


class CombinedFrameTests(unittest.TestCase):
    def setUp(self):
        self.result = load()
        self.df = self.result.df_mr

    def test_september_rows_come_only_from_active_essm(self):
        september = self.df[self.df["month"] == "september"]
        self.assertEqual(set(september["source"]), {mr_sources.SOURCE_ACTIVE_ESSM})
        self.assertEqual(len(september), 3)  # dua tab, partner sama tidak di-dedupe

    def test_october_rows_come_only_from_the_new_database(self):
        october = self.df[self.df["month"] == "october"]
        self.assertEqual(set(october["source"]), {mr_sources.SOURCE_MR_DATABASE})
        self.assertNotIn("Legacy October", set(october["partner_name"]))

    def test_november_rows_come_only_from_the_new_database(self):
        november = self.df[self.df["month"] == "november"]
        self.assertEqual(set(november["source"]), {mr_sources.SOURCE_MR_DATABASE})
        self.assertEqual(
            set(november["partner_name"]), {"Epsilon Co", "Zeta Co"}
        )

    def test_october_is_never_counted_twice(self):
        october = self.df[self.df["month"] == "october"]
        self.assertEqual(len(october), 2)  # Gamma + Delta, duplikat Delta dibuang
        self.assertEqual(metrics.get_total_mr(october), 2)
        self.assertTrue(
            any("tidak dipakai" in note for note in self.result.notes),
            self.result.notes,
        )

    def test_identical_duplicate_rows_are_removed_once(self):
        delta = self.df[self.df["partner_name"] == "Delta Ltd"]
        self.assertEqual(len(delta), 1)
        self.assertTrue(any("duplikat" in note for note in self.result.notes))

    def test_internal_model_columns_match_the_existing_df_mr(self):
        for column in ("partner_name", "pic_aiesec", "date_of_mr", "date_of_mr_raw",
                       "month", "source_tab", "source_row"):
            self.assertIn(column, self.df.columns)
        self.assertTrue(isinstance(self.df["month"].dtype, pd.CategoricalDtype))
        self.assertNotIn("february", list(self.df["month"].cat.categories))
        self.assertNotIn("march", list(self.df["month"].cat.categories))

    def test_schema_mapping_normalizes_values_into_the_internal_model(self):
        gamma = self.df[self.df["partner_name"] == "Gamma Ltd"].iloc[0]
        self.assertEqual(gamma["pic_aiesec"], "Gina")
        self.assertEqual(gamma["month"], "october")
        self.assertEqual(gamma["period"], pd.Period("2026-10", freq="M"))
        self.assertEqual(gamma["date_of_mr"], pd.Timestamp("2026-10-03"))
        self.assertEqual(gamma["date_of_mr_raw"], "2026-10-03")

    def test_rows_without_pic_are_kept_but_never_counted_as_mr(self):
        zeta = self.df[self.df["partner_name"] == "Zeta Co"].iloc[0]
        self.assertTrue(pd.isna(zeta["pic_aiesec"]))
        november = self.df[self.df["month"] == "november"]
        self.assertEqual(len(november), 2)
        self.assertEqual(metrics.get_total_mr(november), 1)

    def test_summary_rows_are_not_records(self):
        self.assertNotIn("TOTAL", set(self.df["partner_name"]))

    def test_month_filters_work_across_both_sources(self):
        frames = {"df_mr": self.df}
        september = periods.filter_by_months(self.df, "september")
        october = periods.filter_by_months(self.df, "october")
        everything = periods.filter_by_months(self.df, "all")
        self.assertEqual(len(september), 3)
        self.assertEqual(len(october), 2)
        self.assertEqual(len(everything), len(self.df))
        self.assertEqual(
            len(periods.filter_by_months(frames["df_mr"], ["september", "october"])), 5
        )

    def test_historical_totals_are_unchanged_by_the_cutover(self):
        """Bulan sebelum Oktober 2026 harus identik dengan hasil Active ESSM saja."""
        legacy_records = []
        for title, grid in ACTIVE_GRIDS:
            legacy_records.extend(preparation.extract_mr_records(grid, title))
        legacy = pd.DataFrame.from_records(legacy_records)
        legacy = legacy[~legacy["month"].isin(preparation.EXCLUDED_MONTHS)]
        for month in ("april", "august", "september"):
            with self.subTest(month=month):
                before = int(legacy[legacy["month"] == month]["pic_aiesec"].notna().sum())
                after = metrics.get_total_mr(self.df[self.df["month"] == month])
                self.assertEqual(before, after)

    def test_monthly_metric_covers_both_sources(self):
        by_month = metrics.get_mr_by_month(self.df).set_index("month")["total_mr"]
        self.assertEqual(int(by_month["september"]), 3)
        self.assertEqual(int(by_month["october"]), 2)
        self.assertEqual(int(by_month["november"]), 1)


class SchemaMappingTests(unittest.TestCase):
    def test_alternative_header_names_map_to_the_same_model(self):
        grid = [
            ["Nama Partner", "Nama Member", "Bulan"],
            ["Alpha", "Rizky", "Oktober 2026"],
            ["Beta", "Sari", "November 2026"],
        ]
        frame, notes, totals = mr_sources.build_df_mr_database([("Tab A", grid)])
        self.assertEqual(list(frame["partner_name"]), ["Alpha", "Beta"])
        self.assertEqual(list(frame["pic_aiesec"]), ["Rizky", "Sari"])
        self.assertEqual(list(frame["month"]), ["october", "november"])
        self.assertEqual(totals["records"], 2)

    def test_month_column_without_a_year_uses_the_term_year(self):
        grid = [["Partner", "PIC", "Month"], ["Alpha", "Rizky", "October"]]
        frame, _, _ = mr_sources.build_df_mr_database(
            [("Tab", grid)], fallback_year=2026
        )
        self.assertEqual(frame.iloc[0]["period"], pd.Period("2026-10", freq="M"))

    def test_january_without_a_year_belongs_to_the_next_calendar_year(self):
        grid = [["Partner", "PIC", "Month"], ["Alpha", "Rizky", "January"]]
        frame, _, _ = mr_sources.build_df_mr_database(
            [("Tab", grid)], fallback_year=2026
        )
        self.assertEqual(frame.iloc[0]["period"], pd.Period("2027-01", freq="M"))

    def test_unmappable_schema_raises_a_clear_error_listing_real_headers(self):
        grid = [["Kolom A", "Kolom B"], ["x", "y"]]
        with self.assertRaises(mr_sources.MRSchemaError) as caught:
            mr_sources.build_df_mr_database([("Tab Aneh", grid)])
        message = str(caught.exception)
        self.assertIn("Kolom A", message)
        self.assertIn("partner_name", message)

    def test_schema_failure_is_never_reported_as_zero_market_research(self):
        result = mr_sources.build_df_mr(
            today=TODAY,
            active_grids=ACTIVE_GRIDS,
            database_grids=[("Tab Aneh", [["Kolom A"], ["x"]])],
        )
        database = result.sources[mr_sources.SOURCE_MR_DATABASE]
        self.assertFalse(database.available)
        self.assertEqual(database.error_kind, "MRSchemaError")
        self.assertTrue(result.df_mr[result.df_mr["month"] == "october"].empty)

    def test_rows_without_a_readable_month_are_reported_not_dropped_silently(self):
        grid = [
            ["Partner", "PIC", "Date of MR"],
            ["Alpha", "Rizky", "2026-10-03"],
            ["Beta", "Sari", "belum diisi"],
        ]
        frame, notes, totals = mr_sources.build_df_mr_database([("Tab", grid)])
        self.assertEqual(totals["records"], 2)
        self.assertTrue(any("tidak punya bulan" in note for note in notes))
        combined, _ = mr_sources.combine_sources(pd.DataFrame(), frame, today=TODAY)
        self.assertEqual(list(combined["partner_name"]), ["Alpha"])

    def test_database_rows_before_the_cutover_are_not_used(self):
        grid = [
            ["Partner", "PIC", "Date of MR"],
            ["Historis", "Rizky", "2026-09-30"],
            ["Baru", "Sari", "2026-10-01"],
        ]
        frame, _, _ = mr_sources.build_df_mr_database([("Tab", grid)])
        combined, notes = mr_sources.combine_sources(pd.DataFrame(), frame, today=TODAY)
        self.assertEqual(list(combined["partner_name"]), ["Baru"])
        self.assertTrue(any("sebelum Oktober 2026" in note for note in notes))


class SectionedPartnershipDatabaseTests(unittest.TestCase):
    """Format Partnership Database BD 26.27, sumber MR sejak Oktober 2026."""

    def build(self, grids=SECTIONED_GRIDS):
        return mr_sources.build_df_mr_database(grids, fallback_year=2026)

    def build_all(self):
        return mr_sources.build_df_mr(
            today=TODAY, active_grids=ACTIVE_GRIDS, database_grids=SECTIONED_GRIDS
        )

    def test_header_below_the_title_and_month_marker_is_found(self):
        header_index, columns = mr_sources.find_header_row(SECTIONED_GRIDS[1][1])
        self.assertEqual(header_index, 4)  # baris 5 di Google Sheets
        self.assertEqual(columns, {"partner_name": 0, "pic_aiesec": 4})

    def test_month_comes_from_the_nearest_section_marker_above(self):
        frame, _, _ = self.build()
        months = dict(zip(frame["partner_name"], frame["month"]))
        self.assertEqual(months["Partner Satu"], "october")
        self.assertEqual(months["Partner Tiga"], "october")
        self.assertEqual(months["Partner Empat"], "november")
        self.assertEqual(months["Partner Lima"], "december")
        self.assertEqual(months["Partner Enam"], "january")

    def test_section_months_without_a_year_follow_the_sheet_order(self):
        frame, _, _ = self.build()
        by_partner = dict(zip(frame["partner_name"], frame["period"]))
        expected = {
            "Partner Satu": "2026-10",
            "Partner Empat": "2026-11",
            "Partner Lima": "2026-12",
            "Partner Enam": "2027-01",
            "Partner Tujuh": "2027-02",
        }
        for partner, period in expected.items():
            with self.subTest(partner=partner):
                self.assertEqual(by_partner[partner], pd.Period(period, freq="M"))

    def test_explicit_year_in_a_marker_wins_over_the_term_year(self):
        grid = sectioned_tab("EwAs", [("Oktober 2026", [partner_row("Partner Satu", "Awa")])])
        frame, _, _ = mr_sources.build_df_mr_database([("EwAs", grid)], fallback_year=2030)
        self.assertEqual(frame.iloc[0]["period"], pd.Period("2026-10", freq="M"))

    def test_repeated_headers_and_month_markers_are_not_records(self):
        frame, _, totals = self.build()
        self.assertEqual(totals["records"], 7)
        self.assertFalse({"Partner Name", "OCTOBER", "NOVEMBER"} & set(frame["partner_name"]))
        _, stats, _ = mr_sources.extract_database_records(
            SECTIONED_GRIDS[1][1], "EwAs", fallback_year=2026
        )
        self.assertEqual(stats["section_markers"], 4)  # OCTOBER ada di atas header pertama
        self.assertEqual(stats["repeated_headers"], 4)
        self.assertEqual(stats["missing_period"], 0)

    def test_aiesec_pic_is_used_never_the_partner_contact_column(self):
        frame, _, _ = self.build()
        self.assertEqual(set(frame["pic_aiesec"]), {"Awa", "Bima", "Citra"})

    def test_partner_contact_column_alone_is_never_accepted_as_aiesec_pic(self):
        header = ["Partner Name", "PIC Name", "Email", "Phone Number"]
        grid = sectioned_tab(
            "ELDs", [("OCTOBER", [["Partner Satu", "Contact Person"]])], header=header
        )
        with self.assertRaises(mr_sources.MRSchemaError) as caught:
            mr_sources.build_df_mr_database([("ELDs", grid)], fallback_year=2026)
        self.assertIn("pic_aiesec", str(caught.exception))

    def test_partner_names_with_month_words_are_still_records(self):
        grid = sectioned_tab("EwAs", [("OCTOBER", [
            partner_row("Mayora", "Awa"),
            partner_row("May Bank", "Awa"),
            partner_row("April", "Bima"),  # nama bulan, tapi baris berisi data lain
        ])])
        frame, _, _ = mr_sources.build_df_mr_database([("EwAs", grid)], fallback_year=2026)
        self.assertEqual(list(frame["partner_name"]), ["Mayora", "May Bank", "April"])
        self.assertEqual(set(frame["month"]), {"october"})

    def test_columns_follow_the_nearest_header_above_each_section(self):
        grid = sectioned_tab("EwAs", [("OCTOBER", [partner_row("Partner Satu", "Awa")])])
        width = len(SECTION_HEADER)
        grid += [
            ["NOVEMBER"] + [""] * (width - 1),
            ["AIESEC PIC", "Partner Name", "PIC Name"] + [""] * (width - 3),
            ["Bima", "Partner Dua", "Contact Person"] + [""] * (width - 3),
        ]
        frame, _, _ = mr_sources.build_df_mr_database([("EwAs", grid)], fallback_year=2026)
        rows = frame.set_index("partner_name")
        self.assertEqual(rows.loc["Partner Satu", "pic_aiesec"], "Awa")
        self.assertEqual(rows.loc["Partner Dua", "pic_aiesec"], "Bima")
        self.assertEqual(rows.loc["Partner Dua", "month"], "november")

    def test_tabs_without_data_yet_are_zero_rows_not_schema_errors(self):
        result = self.build_all()
        database = result.sources[mr_sources.SOURCE_MR_DATABASE]
        self.assertTrue(database.available)
        self.assertIsNone(database.error)
        self.assertFalse(any("skemanya bukan" in note for note in result.notes))
        used = result.df_mr[result.df_mr["source"] == mr_sources.SOURCE_MR_DATABASE]
        self.assertEqual(set(used["source_tab"]), {"EwAs"})

    def test_october_onwards_comes_only_from_the_sectioned_database(self):
        df = self.build_all().df_mr
        october = df[df["month"] == "october"]
        self.assertEqual(set(october["source"]), {mr_sources.SOURCE_MR_DATABASE})
        self.assertNotIn("Legacy October", set(october["partner_name"]))
        self.assertEqual(metrics.get_total_mr(october), 3)
        by_month = metrics.get_mr_by_month(df).set_index("month")["total_mr"]
        self.assertEqual(int(by_month["september"]), 3)  # Active ESSM tidak berubah
        self.assertEqual(int(by_month["november"]), 1)
        self.assertEqual(int(by_month["december"]), 1)
        self.assertEqual(int(by_month["january"]), 1)

    def test_rows_after_the_current_term_are_reported_not_mixed_into_this_term(self):
        result = self.build_all()
        self.assertNotIn("Partner Tujuh", set(result.df_mr["partner_name"]))
        self.assertTrue(
            any("masa jabatan berikutnya" in note for note in result.notes), result.notes
        )

    def test_schema_error_names_the_closest_header_row_not_the_title(self):
        grid = [
            ["PARTNERSHIP DATABASE ELDs", "", ""],
            ["Partner Name", "AIESEC PIC", "Proposal"],
            ["Partner Satu", "Awa", ""],
        ]
        with self.assertRaises(mr_sources.MRSchemaError) as caught:
            mr_sources.build_df_mr_database([("ELDs", grid)])
        message = str(caught.exception)
        self.assertIn("Baris 2", message)
        self.assertIn("month/date", message)
        self.assertIn("'Partner Name'", message)
        self.assertNotIn("partner_name", message)


class SourceFailureTests(unittest.TestCase):
    def test_missing_configuration_is_reported_as_configuration_not_empty_data(self):
        with mock.patch.object(sheets, "market_research_db_configured", return_value=False):
            result = mr_sources.build_df_mr(today=TODAY, active_grids=ACTIVE_GRIDS)
        database = result.sources[mr_sources.SOURCE_MR_DATABASE]
        self.assertFalse(database.available)
        self.assertEqual(database.error_kind, "MRNotConfiguredError")
        self.assertIn(sheets.ENV_MARKET_RESEARCH_DB, database.error)
        # Data historis tetap tampil.
        self.assertEqual(
            metrics.get_total_mr(result.df_mr[result.df_mr["month"] == "september"]), 3
        )

    def test_empty_database_is_distinguishable_from_a_connection_failure(self):
        empty_grid = [("Sheet1", [["Partner", "PIC", "Month"]])]
        result = mr_sources.build_df_mr(
            today=TODAY, active_grids=ACTIVE_GRIDS, database_grids=empty_grid
        )
        database = result.sources[mr_sources.SOURCE_MR_DATABASE]
        self.assertTrue(database.available)
        self.assertIsNone(database.error)
        self.assertEqual(database.rows_used, 0)
        self.assertTrue(any("nol baris" in note for note in database.notes))

    def test_access_denied_is_reported_separately_from_missing_configuration(self):
        def denied():
            raise sheets.MRAccessDeniedError("Service account belum punya akses baca.")

        with mock.patch.object(sheets, "market_research_db_configured", return_value=True), \
                mock.patch.object(sheets, "read_market_research_db_grids", side_effect=denied):
            result = mr_sources.build_df_mr(today=TODAY, active_grids=ACTIVE_GRIDS)
        database = result.sources[mr_sources.SOURCE_MR_DATABASE]
        self.assertEqual(database.error_kind, "MRAccessDeniedError")
        self.assertIn("akses baca", database.error)

    def test_api_errors_are_classified_without_leaking_urls(self):
        class FakeResponse:
            status_code = 403

            @staticmethod
            def json():
                return {"error": {"code": 403, "status": "PERMISSION_DENIED",
                                  "message": "caller does not have permission"}}

        import gspread

        error = gspread.exceptions.APIError(FakeResponse())
        classified = sheets._classify_api_error(error)
        self.assertIsInstance(classified, sheets.MRAccessDeniedError)
        self.assertNotIn("http", str(classified).lower())

    def test_disabled_sheets_api_is_its_own_message(self):
        class FakeResponse:
            status_code = 403

            @staticmethod
            def json():
                return {"error": {"code": 403, "status": "SERVICE_DISABLED",
                                  "message": "Google Sheets API has not been used"}}

        import gspread

        classified = sheets._classify_api_error(gspread.exceptions.APIError(FakeResponse()))
        self.assertIsInstance(classified, sheets.MRAPIError)
        self.assertIn("Google Sheets API", str(classified))

    def test_invalid_configuration_value_is_rejected_with_a_hint(self):
        with mock.patch.object(sheets, "_optional_setting", return_value="https://docs.google.com/spreadsheets/d/e/abc/pubhtml"):
            with self.assertRaises(sheets.MRConfigValueError) as caught:
                sheets.market_research_db_id()
        self.assertIn("published-to-web", str(caught.exception))

    def test_worksheet_name_mistakes_list_the_available_tabs(self):
        class FakeWorksheet:
            def __init__(self, title):
                self.title = title

        class FakeSpreadsheet:
            @staticmethod
            def worksheets():
                return [FakeWorksheet("Sheet1"), FakeWorksheet("Notes")]

        with mock.patch.object(sheets, "open_market_research_db", return_value=FakeSpreadsheet()), \
                mock.patch.object(sheets, "market_research_db_worksheet_names",
                                  return_value=("Tab Salah",)):
            with self.assertRaises(sheets.MRWorksheetError) as caught:
                sheets.market_research_db_worksheets()
        self.assertIn("Sheet1", str(caught.exception))
        self.assertIn("Notes", str(caught.exception))


class ActiveEssmUnchangedTests(unittest.TestCase):
    def test_existing_active_essm_builder_is_untouched(self):
        """preparation.build_df_mr tetap membaca Active ESSM apa adanya."""
        records = preparation.extract_mr_records(ACTIVE_GRIDS[0][1], "[ELDs] Market Research")
        months = [record["month"] for record in records]
        self.assertEqual(months, ["april", "august", "september", "september", "october"])

    def test_source_tab_is_preserved_for_function_provenance(self):
        result = load()
        tabs = set(result.df_mr["source_tab"])
        self.assertIn("[ELDs] Market Research", tabs)
        self.assertIn("MR Oct 2026 onwards", tabs)
        partners = preparation.attach_partner_functions(
            pd.DataFrame([{"partner_name": "Sept Partner B", "function": None,
                           "function_candidates": [None]}]),
            result.df_mr,
        )
        self.assertEqual(partners.iloc[0]["function"], "ELDs")


if __name__ == "__main__":
    unittest.main()
