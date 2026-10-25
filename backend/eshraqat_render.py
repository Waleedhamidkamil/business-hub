"""Shared context for the Eshraqat document family."""
from __future__ import annotations

from . import brand_assets, config


def common_context(*, title: str, doc_no: str, pages: int) -> dict:
    if config.ACTIVE_PROFILE != "eshraqat":
        raise RuntimeError("Eshraqat rendering context requested for another profile")
    return {
        "eshraqat": True,
        "identity": dict(config.ESHRAQAT_IDENTITY),
        "brand": config.PROFILE_ACCENT,
        "logo_data_uri": brand_assets.logo_data_uri(required=True),
        "iso_data_uri": brand_assets.iso_data_uri(),
        "document_title": str(title or "Document"),
        "document_no": str(doc_no or ""),
        "page_count": max(1, int(pages or 1)),
    }


# ONE header (meta) layout for every Eshraqat English document (2026-10-02).
#
# Waleed, looking at IV-0005: "why is the address not below the linked
# quotation? ... the pdf form layout and design should be united between the
# types." Each generator had built its own list, so the three documents put
# the same fields in three different places:
#
#   quotation      Date | Client / Quotation No. | Attention / RFQ No. | Client Address
#   delivery note  DN No. | Date / Client | LPO No. / Source Quotation | Client Address
#   invoice        Invoice No. | Date / LPO No. | Linked Quotation /
#                  Client (full row) / Address (full row, indented differently)
#
# The grid is two columns filled row by row, so every type now reads the same:
#
#   <Document> No.           | Date
#   <client's reference>     | <linked document>
#   Client                   | Address
#
# The client's reference is the LPO (delivery note, invoice) or the RFQ
# (quotation); the linked document is the quotation the DN/invoice came from,
# or, on a quotation, who it is addressed to. Client and its address share the
# bottom row because they are one fact. Labels are the same words on every
# type ("Linked Quotation", not "Source Quotation" on one and "Linked" on the
# other) - the editors call that field "Linked Quotation" too.
#
# Row count is fixed (3), so the header can only be SHORTER than the invoice's
# old 4-row block; the generators' header reserve (`*_TOP_MM`) stays safe.
_DOC_NO_LABEL = {"quote": "Quotation No.", "dn": "DN No.", "inv": "Invoice No."}
_DOC_NO_KEY = {"quote": "quote_no", "dn": "dn_no", "inv": "inv_no"}

# Eshraqat quotations are always addressed to the Admin Manager (user ask), so
# that line is fixed rather than read off the document.
QUOTE_ATTENTION = "Admin Manager"


def meta_rows(kind: str, doc: dict) -> list[dict]:
    """The header rows for an Eshraqat quotation / delivery note / invoice."""
    if kind not in _DOC_NO_LABEL:
        raise ValueError(f"no Eshraqat header layout for {kind!r}")
    doc = doc or {}
    if kind == "quote":
        reference = {"label": "RFQ No.", "value": doc.get("rfq_no", "")}
        linked = {"label": "Attention", "value": QUOTE_ATTENTION}
    else:
        reference = {"label": "LPO No.", "value": doc.get("lpo_no", "")}
        linked = {"label": "Linked Quotation", "value": doc.get("source_quote_no", "")}
    return [
        {"label": _DOC_NO_LABEL[kind], "value": doc.get(_DOC_NO_KEY[kind], "")},
        {"label": "Date", "value": doc.get("date", "")},
        reference,
        linked,
        {"label": "Client", "value": doc.get("client_name", "")},
        {"label": "Address", "value": doc.get("client_address", "")},
    ]
