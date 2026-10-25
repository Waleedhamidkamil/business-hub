"""Derived Linked state for DN/invoice right-panel status precedence."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, database, dn_numbering, inv_numbering
from backend.api import Api


PO_NO = "PO-__STATUS_PRECEDENCE__"
DN_NO = "DN-__STATUS_PRECEDENCE__"
INV_NO = "INV-__STATUS_PRECEDENCE__"
DN_OTHER = "DN-__STATUS_OTHER__"
INV_OTHER = "INV-__STATUS_OTHER__"


def cleanup() -> None:
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("DELETE FROM po_fulfillment WHERE po_no = ?", (PO_NO,))


def main() -> None:
    database.init_db()
    cleanup()
    original_dns = dn_numbering.list_dns_for_month
    original_invs = inv_numbering.list_invoices_for_month
    try:
        database.set_po_fulfillment(PO_NO, [0], "dn", DN_NO, True)
        database.set_po_fulfillment(PO_NO, [0], "inv", INV_NO, True)

        assert DN_NO in database.linked_source_set("dn")
        assert INV_NO in database.linked_source_set("inv")
        assert database.linked_source_set("quote") == set()

        dn_numbering.list_dns_for_month = lambda _y, _m: [
            {"dn_no": DN_NO, "path": "D:/dummy/linked-dn.pdf"},
            {"dn_no": DN_OTHER, "path": "D:/dummy/other-dn.pdf"},
        ]
        inv_numbering.list_invoices_for_month = lambda _y, _m: [
            {"inv_no": INV_NO, "path": "D:/dummy/linked-inv.pdf", "type_code": "MT"},
            {"inv_no": INV_OTHER, "path": "D:/dummy/other-inv.pdf", "type_code": "MT"},
        ]

        api = Api()
        dns = {row["dn_no"]: row for row in api.list_dns_for_month(2026, 7)}
        invs = {row["inv_no"]: row for row in api.list_invoices_for_month(2026, 7)}

        assert dns[DN_NO]["linked"] is True
        assert dns[DN_OTHER]["linked"] is False
        assert invs[INV_NO]["linked"] is True
        assert invs[INV_OTHER]["linked"] is False
    finally:
        dn_numbering.list_dns_for_month = original_dns
        inv_numbering.list_invoices_for_month = original_invs
        cleanup()

    print("document status precedence backend tests passed")


if __name__ == "__main__":
    main()
