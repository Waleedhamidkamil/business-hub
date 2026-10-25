"""Every LPO number on a document should have a record behind it.

Read-only. It names the documents and stops; the fix is to file that LPO or to
correct the document, and neither belongs in a test.

Two things this must NOT complain about:

  - `N/A`. It is a legitimate answer and always will be - 63 of the 64 delivery
    notes in the live Abraj database carry it honestly, because most work is not
    against a purchase order. The LPO number is optional by design.

  - A job note in the `reference` field. Waleed marks an invoice with what the
    job WAS ("CCTV", "CAMP", "Desks", "20260905 SHIP WATER") so he can recognise
    it later; 17 of 48 invoices carry one. That is what `reference` is for - it
    is never printed - and it is a different thing from the client's PO number.
    Two invoices had the same text in BOTH fields on 2026-09-11, which is how
    "20260905 SHIP WATER" came to be sitting in the LPO column.
"""
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))
from backend import database


class NoOrphanLpoNumbers(unittest.TestCase):
    def test_every_referenced_lpo_number_is_on_file(self):
        known = {database._usage_key(r["client_name"], r["lpo_no"])
                 for r in database.list_lpos("")}
        orphans = []
        with database._connect() as conn:
            for table, col in (("dns", "dn_no"), ("invoices", "inv_no")):
                for r in conn.execute(
                        f"SELECT {col} AS no, client_name, lpo_no FROM {table} "
                        "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a')"):
                    if database._usage_key(r["client_name"], r["lpo_no"]) not in known:
                        orphans.append((r["no"], r["client_name"], r["lpo_no"]))
        self.assertEqual(
            [], orphans,
            "these documents name an LPO with no record behind it, so nothing "
            "they deliver or bill can ever roll up:\n"
            + "\n".join(f"    {no}  ({client})  ->  {lpo!r}"
                        for no, client, lpo in sorted(orphans))
            + "\n  Either file that LPO on the Client LPOs screen, or - if what "
              "it carries is a note about the job rather than the client's PO "
              "number - move it to the document's `reference` field and set the "
              "LPO to N/A.")

    def test_a_job_note_has_not_leaked_into_the_lpo_column(self):
        """The two fields answer different questions.

        `reference` is ours, internal, and says what the job was. `lpo_no` is
        the CLIENT's document number and is what every roll-up matches on. The
        same text in both means the second one is a copy that will never match
        anything.
        """
        with database._connect() as conn:
            both = [(r["inv_no"], r["lpo_no"]) for r in conn.execute(
                "SELECT inv_no, lpo_no, reference FROM invoices "
                "WHERE TRIM(COALESCE(lpo_no,'')) NOT IN ('', 'N/A', 'n/a') "
                "AND TRIM(LOWER(COALESCE(lpo_no,''))) = TRIM(LOWER(COALESCE(reference,'')))")]
        self.assertEqual(
            [], both,
            "these invoices carry the same text as both their job reference and "
            "their client's PO number:\n"
            + "\n".join(f"    {no}  ->  {lpo!r}" for no, lpo in sorted(both))
            + "\n  Keep the note in `reference`, which is what it is, and set "
              "the LPO number to N/A.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
