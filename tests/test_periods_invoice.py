"""Regression tests for active partner period union and invoice exceptions."""
import unittest
import pandas as pd
from src import metrics, periods, preparation


def partners():
    rows = []
    for name, end, invoice in (
        ("Alpha", "2026-08", False), ("Beta", "2026-09", False),
        ("Beta ", "2026-10", False), ("Gamma", "2026-10", True),
    ):
        rows.append({"partner_name": name, "has_loa": True,
                     "end_month": pd.Period(end, freq="M"),
                     "end_date": pd.Period(end, freq="M").end_time.normalize(),
                     "stakeholder": "Health Care", "has_proposal": True,
                     "has_mom": True, "has_invoice": invoice})
    return pd.DataFrame(rows)


class PeriodUnionTests(unittest.TestCase):
    def test_all_is_deduplicated_union_and_not_smaller_than_month(self):
        source = partners()
        today = pd.Timestamp("2026-10-04")
        all_rows = periods.active_partner_scope(source, "all", today=today)
        monthly = [periods.active_partner_scope(source, month, today=today)
                   for month in ("august", "september", "october")]
        self.assertEqual(len(all_rows), 3)
        self.assertGreaterEqual(len(all_rows), max(map(len, monthly)))
        self.assertEqual(all_rows.partner_name.map(preparation.normalize_partner_name).nunique(), 3)

    def test_month_excludes_contracts_ended_before_that_month(self):
        september = periods.active_partner_scope(
            partners(), "september", today=pd.Timestamp("2026-10-04")
        )
        self.assertNotIn("Alpha", set(september.partner_name))
        self.assertTrue(all(september.is_active))


class InvoiceExceptionTests(unittest.TestCase):
    def setUp(self):
        self.frame = preparation.apply_reference_date(
            partners().iloc[[0, 3]].copy(), pd.Timestamp("2026-08-31")
        )

    def test_missing_invoice_is_incomplete(self):
        tracker = metrics.get_document_tracker(self.frame)
        alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
        self.assertFalse(alpha.documents_complete)
        self.assertEqual(alpha.documents_missing, 1)

    def test_invoice_on_file_is_complete(self):
        tracker = metrics.get_document_tracker(self.frame)
        self.assertTrue(tracker[tracker.partner_name == "Gamma"].iloc[0].documents_complete)

    def test_in_kind_and_no_membership_satisfy_invoice(self):
        for reason in ("In-Kind", "No Membership"):
            tracker = metrics.get_document_tracker(self.frame, {"alpha": reason})
            alpha = tracker[tracker.partner_name == "Alpha"].iloc[0]
            self.assertTrue(alpha.documents_complete)
            self.assertEqual(alpha.document_compliance_percent, 100)
            summary = metrics.get_document_completeness(self.frame, {"alpha": reason})
            self.assertEqual(summary[summary.document == "invoice"].iloc[0].available, 2)

    def test_clearing_exception_requires_invoice_again(self):
        satisfied = metrics.get_document_tracker(self.frame, {"alpha": "In-Kind"})
        required = metrics.get_document_tracker(self.frame, {})
        self.assertTrue(satisfied[satisfied.partner_name == "Alpha"].iloc[0].documents_complete)
        self.assertFalse(required[required.partner_name == "Alpha"].iloc[0].documents_complete)


if __name__ == "__main__":
    unittest.main()
