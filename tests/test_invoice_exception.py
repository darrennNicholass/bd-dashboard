"""Invoice Exception: an in-kind partner must read N/A, never Missing.

Tes ini menutup akar masalahnya di tiga lapisan sekaligus: normalisasi nilai,
pembacaan dari kedua backend penyimpanan, dan label yang tampil di section
Invoice Exception.
"""
import ast
from pathlib import Path
import unittest
from uuid import uuid4

import pandas as pd

from src import metrics, preparation
from src.snd_backup import verified_cutover_partners
from src.snd_cloud_store import GoogleSheetsSNDStore
from src.snd_store import SNDStore
from tests.fake_sheets import FakeBook

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _app_namespace():
    """Pinjam fungsi murni dari app.py tanpa menjalankan Streamlit."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    wanted_functions = {"invoice_status_label"}
    wanted_assignments = {"INVOICE_REQUIRED_LABEL"}
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted_functions)
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in wanted_assignments
            for target in node.targets))
    ]
    namespace = {"preparation": preparation, "pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_PATH), "exec"), namespace)
    return namespace


APP = _app_namespace()
invoice_status_label = APP["invoice_status_label"]
INVOICE_REQUIRED_LABEL = APP["INVOICE_REQUIRED_LABEL"]


def partners():
    """Dua partner aktif: satu tanpa invoice, satu dengan invoice lengkap."""
    rows = []
    for name, invoice in (("Alpha", False), ("Gamma", True)):
        rows.append({
            "partner_name": name, "has_loa": True,
            "end_month": pd.Period("2026-12", freq="M"),
            "end_date": pd.Period("2026-12", freq="M").end_time.normalize(),
            "stakeholder": "Health Care", "has_proposal": True,
            "has_mom": True, "has_invoice": invoice,
        })
    frame = pd.DataFrame(rows)
    return preparation.apply_reference_date(frame, pd.Timestamp("2026-10-15"))


class CanonicalReasonTests(unittest.TestCase):
    def test_every_spelling_of_in_kind_is_one_reason(self):
        for value in ("inkind", "in-kind", "in kind", "IN KIND", "In-Kind",
                      " In Kind ", "InKind", "IN-KIND"):
            with self.subTest(value=value):
                self.assertEqual(preparation.canonical_invoice_reason(value), "In-Kind")

    def test_every_spelling_of_no_membership_is_one_reason(self):
        for value in ("no membership", "No Membership", "nomembership",
                      "NO-MEMBERSHIP", "non membership"):
            with self.subTest(value=value):
                self.assertEqual(
                    preparation.canonical_invoice_reason(value), "No Membership"
                )

    def test_values_meaning_invoice_is_required_are_not_exceptions(self):
        for value in (None, "", "   ", "Required", "required", "-", "nan",
                      float("nan"), pd.NA):
            with self.subTest(value=value):
                self.assertIsNone(preparation.canonical_invoice_reason(value))

    def test_unknown_text_never_becomes_an_exception(self):
        for value in ("barteran?", "maybe", "pending", "kind of"):
            with self.subTest(value=value):
                self.assertIsNone(preparation.canonical_invoice_reason(value))

    def test_punctuation_and_spacing_are_ignored_not_treated_as_unknown(self):
        """"in kind?" tetap berarti In-Kind: tanda baca bukan pembeda arti."""
        self.assertEqual(preparation.canonical_invoice_reason("in kind?"), "In-Kind")
        self.assertEqual(preparation.canonical_invoice_reason("In_Kind"), "In-Kind")

    def test_map_normalizes_values_and_drops_unknown_reasons(self):
        mapped = preparation.invoice_exception_map(
            {"alpha": "inkind", "beta": "Required", "gamma": "no membership",
             "delta": "unclear"}
        )
        self.assertEqual(mapped, {"alpha": "In-Kind", "gamma": "No Membership"})


class DocumentTrackerTests(unittest.TestCase):
    def setUp(self):
        self.frame = partners()

    def test_inkind_lowercase_is_not_missing(self):
        tracker = metrics.get_document_tracker(self.frame, {"alpha": "inkind"})
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertEqual(alpha.invoice_exception, "In-Kind")
        self.assertTrue(alpha.invoice_satisfied)
        self.assertTrue(alpha.documents_complete)
        self.assertEqual(alpha.documents_missing, 0)
        self.assertEqual(
            invoice_status_label(alpha.has_invoice, alpha.invoice_exception),
            "N/A — In-Kind",
        )

    def test_required_partner_without_invoice_stays_missing(self):
        tracker = metrics.get_document_tracker(self.frame, {})
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertFalse(alpha.invoice_satisfied)
        self.assertEqual(alpha.documents_missing, 1)
        self.assertEqual(
            invoice_status_label(alpha.has_invoice, alpha.invoice_exception), "Missing"
        )

    def test_completed_invoice_keeps_its_existing_status(self):
        tracker = metrics.get_document_tracker(self.frame, {})
        gamma = tracker[tracker.partner_name == "Gamma"].iloc[0]
        self.assertTrue(gamma.documents_complete)
        self.assertEqual(
            invoice_status_label(gamma.has_invoice, gamma.invoice_exception), "On file"
        )

    def test_no_membership_follows_the_same_not_required_rule(self):
        tracker = metrics.get_document_tracker(self.frame, {"alpha": "no membership"})
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertEqual(alpha.invoice_exception, "No Membership")
        self.assertTrue(alpha.documents_complete)
        self.assertEqual(
            invoice_status_label(alpha.has_invoice, alpha.invoice_exception),
            "N/A — No Membership",
        )

    def test_unknown_reason_keeps_the_invoice_required(self):
        tracker = metrics.get_document_tracker(self.frame, {"alpha": "mungkin"})
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertTrue(pd.isna(alpha.invoice_exception))
        self.assertFalse(alpha.documents_complete)

    def test_partner_name_spacing_and_case_still_match(self):
        tracker = metrics.get_document_tracker(self.frame, {"  ALPHA  ": "in kind"})
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertEqual(alpha.invoice_exception, "In-Kind")

    def test_completeness_summary_counts_the_exception_as_satisfied(self):
        summary = metrics.get_document_completeness(self.frame, {"alpha": "inkind"})
        invoice = summary[summary.document == "invoice"].iloc[0]
        self.assertEqual(invoice.available, 2)
        self.assertEqual(invoice.missing, 0)


class SqliteStoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).with_name(f".tmp_invoice_{uuid4().hex}.sqlite3")
        self.store = SNDStore(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_legacy_lowercase_value_is_read_as_an_exception(self):
        # Tiru data lama / hasil migrasi yang ejaannya bukan bentuk kanonik.
        with self.store._connect() as connection:
            connection.execute(
                "INSERT INTO invoice_exceptions VALUES (?,?,?)",
                ("alpha", "inkind", "2026-10-01T00:00:00+00:00"),
            )
        self.assertEqual(self.store.get_invoice_exceptions(), {"alpha": "In-Kind"})
        tracker = metrics.get_document_tracker(
            partners(), self.store.get_invoice_exceptions()
        )
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertTrue(alpha.documents_complete)

    def test_writes_are_stored_in_canonical_form(self):
        self.store.set_invoice_exception("Alpha", "in kind")
        self.assertEqual(self.store.get_invoice_exceptions(), {"alpha": "In-Kind"})

    def test_clearing_removes_the_exception(self):
        self.store.set_invoice_exception("Alpha", "In-Kind")
        self.store.set_invoice_exception("Alpha", None)
        self.assertEqual(self.store.get_invoice_exceptions(), {})

    def test_invalid_reason_is_still_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_invoice_exception("Alpha", "diskon")


class GoogleSheetsStoreTests(unittest.TestCase):
    """The production backend: this read filter was the root cause."""

    def store(self, book):
        return GoogleSheetsSNDStore(
            "invoice-test", spreadsheet=book, expected_cutover_id="cut-invoice"
        )

    def setUp(self):
        self.book = FakeBook()
        # Jalankan cutover yang sebenarnya supaya store berstatus produksi.
        verified_cutover_partners(
            {}, GoogleSheetsSNDStore("invoice-test", spreadsheet=self.book),
            apply=True, migration_id="cut-invoice", source_backup_sha256="0" * 64,
        )

    def test_legacy_lowercase_value_is_no_longer_dropped(self):
        store = self.store(self.book)
        store._patch("Alpha", {"invoice_exception": "inkind"})
        reread = self.store(self.book)
        self.assertEqual(reread.get_invoice_exceptions(), {"alpha": "In-Kind"})
        tracker = metrics.get_document_tracker(
            partners(), reread.get_invoice_exceptions()
        )
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertTrue(alpha.invoice_satisfied)
        self.assertEqual(
            invoice_status_label(alpha.has_invoice, alpha.invoice_exception),
            "N/A — In-Kind",
        )

    def test_saving_from_the_editor_persists_and_reads_back(self):
        store = self.store(self.book)
        store.set_invoice_exception("Alpha", "In-Kind")
        self.assertEqual(self.store(self.book).get_invoice_exceptions(), {"alpha": "In-Kind"})

    def test_clearing_from_the_editor_requires_the_invoice_again(self):
        store = self.store(self.book)
        store.set_invoice_exception("Alpha", "In-Kind")
        store.set_invoice_exception("Alpha", None)
        exceptions = self.store(self.book).get_invoice_exceptions()
        self.assertEqual(exceptions, {})
        tracker = metrics.get_document_tracker(partners(), exceptions)
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertFalse(alpha.documents_complete)

    def test_invalid_reason_is_still_rejected(self):
        with self.assertRaises(ValueError):
            self.store(self.book).set_invoice_exception("Alpha", "diskon")


class EditorLabelTests(unittest.TestCase):
    def test_dropdown_options_match_the_canonical_reasons(self):
        self.assertEqual(
            preparation.INVOICE_EXCEPTION_REASONS, ("In-Kind", "No Membership")
        )
        self.assertEqual(INVOICE_REQUIRED_LABEL, "Required")

    def test_label_rule_order_keeps_completed_invoices_visible(self):
        self.assertEqual(invoice_status_label(True, "In-Kind"), "On file")
        self.assertEqual(invoice_status_label(False, "In-Kind"), "N/A — In-Kind")
        self.assertEqual(invoice_status_label(False, None), "Missing")
        self.assertEqual(invoice_status_label(False, float("nan")), "Missing")


if __name__ == "__main__":
    unittest.main()
