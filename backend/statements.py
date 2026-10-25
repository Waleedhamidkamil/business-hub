"""
Monthly client statement - one PDF per client per month (2026-10-06).

Waleed sends a client one file a month: a summary page listing every invoice of
that month (delivery note, purchase orders, cost, charged, profit, paid or not),
followed by the full copy of every document it names. Each number on the summary
page is a link to that document's pages, and every document has a bookmark.

The first two (TDC August and September 2026) were made by a one-off script in
a temp folder, with the client and the month written into it. This is that
script made general and given a button in Insights (`static/analytics.js`).

Where the numbers come from - nothing here does its own arithmetic:
    the invoices and their charged / spent / profit   insights._collect, so the
        statement and the Insights invoice table can never disagree
    paid                                              the payment record
    which copy is attached                            a PAID invoice uses the
        client's returned copy when one is on file (signed_copies), otherwise
        the PDF we issued; an unpaid invoice always uses the one we issued
    delivery note                                     invoices.source_dn_no
        (the client's signed copy when there is one)
    purchase orders                                   po_fulfillment rows that
        name the invoice

How the PDF is made, with the app's own tools only (no new dependency):
    1. the summary page is HTML rendered by headless Chrome (pdf_engine), with
       each document number an <a href> to a placeholder address;
    2. photos (a returned copy can be a JPG/PNG) become A4 pages in ONE more
       Chrome pass;
    3. pypdf joins the summary and every document, then turns each placeholder
       link into a jump to that document's first page. Chrome placed the links,
       so their positions are exact; nothing is searched for on the page.
"""
from __future__ import annotations

import base64
import calendar
import html
import mimetypes
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from . import client_master, config, insights, pdf_engine, pdf_lock

LINK_PREFIX = "https://statement.invalid/doc/"
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _dmy(value: str) -> tuple[int, int, int]:
    """(year, month, day) of a D/M/YYYY date, (0, 0, 0) when unreadable."""
    try:
        d, m, y = (int(p) for p in str(value or "").strip().split("/"))
        return y, m, d
    except (TypeError, ValueError):
        return 0, 0, 0


def _short(value: str) -> str:
    """'5/8/2026' -> '05/08'. Anything unreadable is shown as it is."""
    y, m, d = _dmy(value)
    return f"{d:02}/{m:02}" if y else str(value or "")


def _natural(value: str) -> list:
    """INV-9 before INV-10, A-9 before A-10."""
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r"(\d+)", str(value or ""))]


def _money(v: float) -> str:
    return f"{v:,.2f}"


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _safe_name(name: str) -> str:
    return _UNSAFE_FILENAME.sub("-", str(name or "").strip()).strip(" .") or "Client"


def output_path(client: str, year: int, month: int) -> Path:
    """<documents root>\\REPORTS\\2026\\AUG\\TDC - August 2026.pdf - the folder
    the first statements were filed in."""
    return (Path(config.DOCUMENTS_ROOT) / "REPORTS" / str(year)
            / calendar.month_abbr[month].upper()
            / f"{_safe_name(client)} - {calendar.month_name[month]} {year}.pdf")


def _client_where(client: str) -> tuple[str, tuple]:
    keys = tuple(client_master.alias_keys_for(client))
    if not keys:
        keys = (client_master.normalize(client),)
    return "client_key(client_name) IN (" + ",".join("?" for _ in keys) + ")", keys


# --------------------------------------------------------------------------- #
# which months have something to state
# --------------------------------------------------------------------------- #

def statement_months(client: str) -> list[dict[str, Any]]:
    """Every month this client has issued invoices in, newest first."""
    client = (client or "").strip()
    if not client:
        return []
    where, args = _client_where(client)
    with insights._connect() as conn:
        rows = conn.execute(
            "SELECT year, month, COUNT(*) AS n FROM invoices "
            "WHERE TRIM(COALESCE(pdf_path,'')) <> '' AND " + where +
            " GROUP BY year, month ORDER BY year DESC, month DESC", args).fetchall()
    out = []
    for r in rows:
        y, m = int(r["year"] or 0), int(r["month"] or 0)
        if not (y and 1 <= m <= 12):
            continue
        out.append({"year": y, "month": m, "invoices": int(r["n"]),
                    "label": f"{calendar.month_name[m]} {y}",
                    "exists": output_path(client, y, m).is_file()})
    return out


# --------------------------------------------------------------------------- #
# gather - what goes in, decided before anything is rendered
# --------------------------------------------------------------------------- #

def gather(client: str, year: int, month: int) -> dict[str, Any]:
    """Everything the statement says, as data. No file is read or written.

    Kept apart from rendering so the decisions (which invoices, which copy,
    which links) can be tested without Chrome.
    """
    client = (client or "").strip()
    year, month = int(year), int(month)
    docs, _ctx = insights._collect([(year, month)], client)
    invs = [d for d in docs if d["kind"] == "inv"]
    invs.sort(key=lambda d: (_dmy(d["date"]), _natural(d["no"])))
    current = {d["no"] for d in invs}

    where, args = _client_where(client)
    with insights._connect() as conn:
        inv_rows = {r["inv_no"]: dict(r) for r in conn.execute(
            "SELECT inv_no, year, month, source_dn_no, pdf_path FROM invoices "
            "WHERE TRIM(COALESCE(pdf_path,'')) <> '' AND " + where, args)}
        dns = {r["dn_no"]: dict(r) for r in conn.execute(
            "SELECT dn_no, dn_date, pdf_path FROM dns")}
        pos = {r["po_no"]: dict(r) for r in conn.execute(
            "SELECT po_no, po_date, year, month, pdf_path FROM pos")}
        copies = {(r["kind"], r["doc_no"]): r["path"] for r in conn.execute(
            "SELECT kind, doc_no, path FROM signed_copies")}
        links = [(r["po_no"], r["source_no"]) for r in conn.execute(
            "SELECT DISTINCT po_no, source_no FROM po_fulfillment "
            "WHERE source_kind = 'inv'")]

    pos_of: dict[str, list[str]] = {}
    for po_no, inv_no in links:
        if po_no in pos:
            pos_of.setdefault(inv_no, []).append(po_no)

    rows: list[dict[str, Any]] = []
    for i, d in enumerate(invs, 1):
        no = d["no"]
        rec = inv_rows.get(no) or {}
        paid = bool(d.get("paid"))
        returned = copies.get(("inv", no))
        use_returned = paid and bool(returned)
        dn = (rec.get("source_dn_no") or "").strip()
        rows.append({
            "n": i, "no": no, "date": d["date"], "paid": paid,
            "copy": returned if use_returned else (rec.get("pdf_path") or d.get("pdf") or ""),
            "copy_role": ("paid copy" if use_returned else
                          "issued copy (paid, no separate copy)" if paid else "original (unpaid)"),
            "paid_copy_used": use_returned,
            "paid_no_copy": paid and not returned,
            "dn": dn if dn in dns else "",
            "dn_date": (dns.get(dn) or {}).get("dn_date", ""),
            "dn_copy": (copies.get(("dn", dn)) or (dns.get(dn) or {}).get("pdf_path") or "") if dn in dns else "",
            "pos": [(p, pos[p]["po_date"] or "") for p in sorted(set(pos_of.get(no, [])), key=_natural)],
            "charged": float(d["charged"]), "cost": float(d["spent"]),
            "profit": float(d["charged"]) - float(d["spent"]),
            "costed": bool(d.get("costed")),
        })

    # Purchase orders dated this month whose invoice fell in ANOTHER month - a
    # reader checking the month's orders would otherwise not find them.
    outside: list[dict[str, Any]] = []
    for po_no, po in pos.items():
        if (po.get("year"), po.get("month")) != (year, month):
            continue
        elsewhere = sorted({inv for p, inv in links if p == po_no
                            and inv in inv_rows and inv not in current}, key=_natural)
        if elsewhere:
            outside.append({"po": po_no, "date": po.get("po_date") or "",
                            "copy": po.get("pdf_path") or "",
                            "invoiced_on": [(n, inv_rows[n]["month"], inv_rows[n]["year"])
                                            for n in elsewhere]})
    outside.sort(key=lambda x: _natural(x["po"]))

    # The attachment order: invoices, then delivery notes, then purchase orders.
    attach: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, no: str, path: str, role: str) -> None:
        if no and no not in seen:
            seen.add(no)
            attach.append({"kind": kind, "no": no, "path": path or "", "role": role})

    for r in rows:
        add("inv", r["no"], r["copy"], r["copy_role"])
    for r in rows:
        add("dn", r["dn"], r["dn_copy"], "delivery note")
    for r in rows:
        for p, _date in r["pos"]:
            add("po", p, pos[p]["pdf_path"], "purchase order")
    for x in outside:
        add("po", x["po"], x["copy"], "purchase order invoiced outside the month")

    charged = sum(r["charged"] for r in rows)
    cost = sum(r["cost"] for r in rows)
    return {
        "client": client, "year": year, "month": month,
        "label": f"{calendar.month_name[month]} {year}",
        "rows": rows, "outside": outside, "attach": attach,
        "charged": charged, "cost": cost, "profit": charged - cost,
        "paid": sum(1 for r in rows if r["paid"]),
        "paid_usd": sum(r["charged"] for r in rows if r["paid"]),
        "pending_usd": sum(r["charged"] for r in rows if not r["paid"]),
        "paid_no_copy": [r["no"] for r in rows if r["paid_no_copy"]],
        "uncosted": [r["no"] for r in rows if not r["costed"]],
    }


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #

_COVER_CSS = """
@page { size: A4 landscape; margin: 9mm 10mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, "Segoe UI", sans-serif; color: #111; font-size: 7.6pt; }
.head { display: flex; align-items: center; gap: 10px; border-bottom: 1.3pt solid #203864; padding-bottom: 6px; }
.head img { height: 36px; }
.head .t { flex: 1; }
.head h1 { margin: 0; color: #203864; font-size: 15pt; }
.head .sub, .head .meta { color: #777; font-size: 7.6pt; margin-top: 2px; }
.head .who { text-align: right; }
.head .who b { font-size: 12pt; display: block; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th { background: #203864; color: #fff; font-weight: normal; text-align: left; font-size: 7.2pt; }
th, td { border: 0.4pt solid #c9cddd; padding: 2.5px 4px; vertical-align: top; }
tbody tr:nth-child(even) td { background: #f5f6f9; }
tr.total td { background: #e9edf4; font-weight: bold; }
tr { break-inside: avoid; }
td.num { text-align: right; white-space: nowrap; }
a { color: #203864; }
.dim { color: #777; }
.small { font-size: 6pt; color: #777; }
h2 { color: #203864; font-size: 9pt; margin: 12px 0 0; }
.foot { margin-top: 10px; border-top: 0.6pt solid #c9cddd; padding-top: 5px; line-height: 1.45; }
"""


def _cover_html(data: dict[str, Any], linkable: dict[str, int], logo: str | None,
                company: str) -> str:
    def link(no: str, date: str = "") -> str:
        text = _e(no)
        if no in linkable:
            text = f'<a href="{LINK_PREFIX}{linkable[no]}">{text}</a>'
        return text + (f' <span class="dim">{_e(_short(date))}</span>' if date else "")

    body = []
    for r in data["rows"]:
        pct = f"{r['profit'] / r['charged'] * 100:.1f}%" if r["charged"] else "0.0%"
        body.append(
            "<tr>"
            f"<td>{r['n']}</td>"
            f"<td>{link(r['no'])}" + ('<br><span class="small">paid copy</span>' if r["paid_copy_used"] else "") + "</td>"
            f"<td>{_e(_short(r['date']))}</td>"
            f"<td>{link(r['dn'], r['dn_date']) if r['dn'] else ''}</td>"
            f"<td>{' '.join(link(p, d) for p, d in r['pos'])}</td>"
            f"<td class=\"num\">{_money(r['cost'])}{'' if r['costed'] else '*'}</td>"
            f"<td class=\"num\">{_money(r['charged'])}</td>"
            f"<td class=\"num\">{_money(r['profit'])}</td>"
            f"<td class=\"num\">{pct}</td>"
            f"<td>{'<b>PAID</b>' if r['paid'] else '<span class=dim>Pending</span>'}</td>"
            "</tr>")
    n = len(data["rows"])
    tpct = f"{data['profit'] / data['charged'] * 100:.1f}%" if data["charged"] else "0.0%"
    body.append(
        '<tr class="total"><td></td><td></td><td></td><td></td>'
        f"<td>TOTAL - {n} invoice{'' if n == 1 else 's'}</td>"
        f"<td class=\"num\">{_money(data['cost'])}</td>"
        f"<td class=\"num\">{_money(data['charged'])}</td>"
        f"<td class=\"num\">{_money(data['profit'])}</td>"
        f"<td class=\"num\">{tpct}</td><td>{data['paid']} paid</td></tr>")

    outside = ""
    if data["outside"]:
        orow = "".join(
            f"<tr><td>{link(x['po'])}</td><td>{_e(_short(x['date']))}</td><td>"
            + ", ".join(f"{_e(no)} - {calendar.month_abbr[m]} {y}" for no, m, y in x["invoiced_on"])
            + "</td></tr>" for x in data["outside"])
        outside = ("<h2>Purchase orders raised this month, invoiced outside it</h2>"
                   '<table><thead><tr><th style="width:16%">PURCHASE ORDER</th>'
                   '<th style="width:9%">DATE</th><th>INVOICED ON</th></tr></thead>'
                   f"<tbody>{orow}</tbody></table>")

    notes = ["<b>Cost</b> is the recorded cost; <b>Charged</b> is the invoice value, both in USD. "
             "IQD amounts use the saved conversion rate for each invoice. Paid status is from the "
             "app payment record. Paid invoices use their returned paid copy when one is on file; "
             "unpaid invoices use their original."]
    if data["paid_no_copy"]:
        notes.append("<b>No separate paid copy on file:</b> " + _e(", ".join(data["paid_no_copy"]))
                     + ". Their issued PDFs are included.")
    if data["uncosted"]:
        notes.append("<b>* Incomplete costs:</b> " + _e(", ".join(data["uncosted"]))
                     + ". The total includes recorded costs only.")
    if data.get("missing"):
        notes.append("<b>File not found, not included:</b> " + _e(", ".join(data["missing"])) + ".")
    notes.append(f"<b>Paid: ${_money(data['paid_usd'])} | Pending: ${_money(data['pending_usd'])}</b>")

    today = datetime.now().strftime("%d/%m/%Y")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_COVER_CSS}</style></head><body>
<div class="head">
  {f'<img src="{logo}" alt="">' if logo else ''}
  <div class="t"><h1>Monthly Statement - {_e(data['label'])}</h1>
    <div class="sub">{_e(company)} | Document numbers link to full copies inside this file</div></div>
  <div class="who"><b>{_e(data['client'])}</b>
    <div class="meta">{n} invoice{'' if n == 1 else 's'} | updated {today}</div></div>
</div>
<table><thead><tr>
  <th style="width:3%">#</th><th style="width:13%">INVOICE</th><th style="width:5%">DATE</th>
  <th style="width:10%">DELIVERY NOTE</th><th>PURCHASE ORDERS</th>
  <th style="width:8%">COST $</th><th style="width:8%">CHARGED $</th><th style="width:8%">PROFIT $</th>
  <th style="width:5%">%</th><th style="width:7%">STATUS</th>
</tr></thead><tbody>{''.join(body)}</tbody></table>
{outside}
<div class="foot">{'<br>'.join(notes)}</div>
</body></html>"""


def _images_html(paths: list[Path]) -> str:
    pages = []
    for p in paths:
        mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        uri = f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("ascii")
        pages.append(f'<div class="pg"><img src="{uri}" alt=""></div>')
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            "@page { size: A4; margin: 0; } body { margin: 0; }"
            ".pg { width: 210mm; height: 297mm; display: flex; align-items: center;"
            " justify-content: center; overflow: hidden; }"
            ".pg + .pg { break-before: page; }"
            ".pg img { max-width: 200mm; max-height: 287mm; object-fit: contain; }"
            "</style></head><body>" + "".join(pages) + "</body></html>")


def _company() -> str:
    try:
        lines = (getattr(config, "INV_COMPANY", {}) or {}).get("name_lines") or []
        return str(lines[0]).strip() if lines else ""
    except Exception:
        return ""


def _logo() -> str | None:
    try:
        from . import brand_assets
        return brand_assets.logo_data_uri()
    except Exception:
        return None


def _rewrite_links(writer, cover_pages: int, targets: dict[int, int]) -> int:
    """Turn each placeholder URI on the summary pages into a jump to its page."""
    from pypdf.generic import ArrayObject, NameObject
    made = 0
    for i in range(cover_pages):
        annots = writer.pages[i].get("/Annots")
        if not annots:
            continue
        keep = ArrayObject()
        for ref in annots.get_object():
            a = ref.get_object()
            action = a.get("/A")
            uri = str(action.get_object().get("/URI", "")) if action else ""
            if not uri.startswith(LINK_PREFIX):
                keep.append(ref)
                continue
            try:
                dest = targets[int(uri[len(LINK_PREFIX):])]
            except (KeyError, ValueError):
                continue                       # a link to nothing is dropped
            del a["/A"]
            a[NameObject("/Dest")] = ArrayObject(
                [writer.pages[dest].indirect_reference, NameObject("/Fit")])
            keep.append(ref)
            made += 1
        writer.pages[i][NameObject("/Annots")] = keep
    return made


def build(client: str, year: int, month: int, out_path: Path | None = None) -> dict[str, Any]:
    """Make the statement PDF. Returns what it contains and where it went."""
    from pypdf import PdfReader, PdfWriter

    data = gather(client, year, month)
    if not data["rows"]:
        return {"ok": False, "error": f"{data['client']} has no issued invoices in {data['label']}."}
    out = Path(out_path) if out_path else output_path(data["client"], data["year"], data["month"])

    present, missing = [], []
    for a in data["attach"]:
        p = Path(a["path"]) if a["path"] else None
        if p and p.is_file():
            present.append({**a, "file": p})
        else:
            missing.append(a["no"])
    data["missing"] = missing
    linkable = {a["no"]: i for i, a in enumerate(present)}

    work = Path(tempfile.mkdtemp(prefix="statement_"))
    try:
        cover_pdf = pdf_engine.write_pdf(
            _cover_html(data, linkable, _logo(), _company()), work / "cover.pdf")
        images = [a["file"] for a in present if a["file"].suffix.lower() in _IMAGE_SUFFIXES]
        image_pages: dict[Path, Any] = {}
        if images:
            ir = PdfReader(str(pdf_engine.write_pdf(_images_html(images), work / "images.pdf")))
            if len(ir.pages) != len(images):
                return {"ok": False, "error": "A returned photo did not fit on one page; "
                                              "nothing was saved."}
            image_pages = {p: ir.pages[i] for i, p in enumerate(images)}

        writer = PdfWriter()
        cover = PdfReader(str(cover_pdf))
        for pg in cover.pages:
            writer.add_page(pg)
        cover_pages = len(cover.pages)

        starts: dict[int, int] = {}
        bookmarks = []
        for i, a in enumerate(present):
            starts[i] = len(writer.pages)
            if a["file"] in image_pages:
                writer.add_page(image_pages[a["file"]])
            else:
                for pg in PdfReader(str(a["file"])).pages:
                    writer.add_page(pg)
            bookmarks.append((f"{a['no']} - {a['role']}", starts[i]))
        links = _rewrite_links(writer, cover_pages, starts)
        writer.add_outline_item("Summary", 0)
        for title, page in bookmarks:
            writer.add_outline_item(title, page)
        writer.add_metadata({
            "/Title": f"{data['client']} Monthly Statement - {data['label']}",
            "/Author": _company(),
            "/Subject": f"Invoices, payment status and supporting documents, {data['label']}",
        })
        staged = work / "statement.pdf"
        with staged.open("wb") as fh:
            writer.write(fh)
        pdf_lock.lock(staged)                  # like every PDF the app makes
        pages = len(PdfReader(str(staged)).pages)

        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(staged, out)
        except PermissionError:
            return {"ok": False, "error": f"{out.name} is open in another program. "
                                          "Close it and try again."}
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return {
        "ok": True, "path": str(out), "pages": pages, "links": links,
        "cover_pages": cover_pages,
        "client": data["client"], "label": data["label"],
        "invoices": len(data["rows"]), "paid": data["paid"],
        "pending": len(data["rows"]) - data["paid"],
        "charged": round(data["charged"], 2), "cost": round(data["cost"], 2),
        "profit": round(data["profit"], 2),
        "paid_usd": round(data["paid_usd"], 2), "pending_usd": round(data["pending_usd"], 2),
        "documents": len(present), "missing": missing,
        "paid_no_copy": data["paid_no_copy"], "uncosted": data["uncosted"],
        "size_mb": round(os.path.getsize(out) / 1048576, 1),
    }
