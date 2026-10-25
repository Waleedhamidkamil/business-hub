import base64
import importlib
import re
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def reload_quote_stack():
    from backend import brand_assets, config, eshraqat_render, quote_generator, signing

    importlib.reload(config)
    importlib.reload(brand_assets)
    importlib.reload(eshraqat_render)
    importlib.reload(signing)
    return importlib.reload(quote_generator), config, brand_assets


def signing_overlay() -> dict:
    return {
        "slots": {
            "main": {
                "sig_index": 1,
                "stamp": False,
                "sig_dx_mm": 0,
                "sig_dy_mm": 0,
                "sig_rot": 0,
                "stamp_vs_sig_y_mm": 0,
            }
        },
        "size": {
            "stamp_w_mm": 46.5,
            "stamp_h_mm": 29.0,
            "sig_w_mm": 55.0,
            "sig_h_mm": 22.0,
        },
    }


def base_quote(quote_type: str, index: int) -> dict:
    rows = {
        "materials": {
            "item": "Pipe",
            "description": "Carbon steel pipe",
            "unit": "pcs",
            "qty": "2",
            "rate": "12.50",
            "image": PNG_DATA_URI,
        },
        "equipment": {
            "equipment": "Mobile crane",
            "specs": "50 ton",
            "rate": "4500",
            "image": PNG_DATA_URI,
        },
        "manpower": {"role": "Mechanical supervisor", "unit": "Per Hour", "rate": "18"},
        "service": {
            "description": "Site inspection",
            "unit": "Visit",
            "rate": "300",
            "remarks": "Includes report",
        },
    }
    return {
        "quote_no": f"QT-{index:04d}",
        "date": "14/07/2026",
        "quote_type": quote_type,
        "client_name": "Horizon Energy",
        "rfq_no": f"RFQ-{700 + index}",
        "attention_to": "Ms. Noor Al-Sabah",
        "client_address": "Basra Energy Park",
        "currency": "USD",
        "items": [rows[quote_type]],
        "terms": [f"Approved {quote_type} commercial term"],
        "layout": {"autofit_rows": quote_type == "equipment"},
        "signing_overlay": signing_overlay(),
    }


def assert_layout_roles(quote_generator) -> None:
    schema = quote_generator._schema_for("materials")
    terms = ["Currency: USD"]

    def items(count: int) -> list[dict]:
        return [
            {
                "item": f"Item {i}",
                "description": "Standard material",
                "unit": "pcs",
                "qty": "1",
                "rate": "10",
            }
            for i in range(1, count + 1)
        ]

    single = quote_generator.build_layout(items(1), terms, schema, autofit=False)
    assert len(single) == 1
    assert single[0]["show_header"] is True
    assert single[0]["show_footer"] is True
    assert single[0]["is_last"] is True

    one_page_counts = [
        count
        for count in range(1, 101)
        if len(quote_generator.build_layout(items(count), terms, schema, autofit=False)) == 1
    ]
    boundary = max(one_page_counts)
    assert len(quote_generator.build_layout(items(boundary), terms, schema, autofit=False)) == 1
    assert len(quote_generator.build_layout(items(boundary + 1), terms, schema, autofit=False)) > 1

    two_page_count = next(
        count
        for count in range(boundary + 1, 101)
        if len(quote_generator.build_layout(items(count), terms, schema, autofit=False)) == 2
    )
    two_pages = quote_generator.build_layout(items(two_page_count), terms, schema, autofit=False)
    assert two_pages[0]["show_header"] is True and two_pages[0]["show_footer"] is False
    assert two_pages[-1]["show_header"] is False and two_pages[-1]["show_footer"] is True
    assert two_pages[-1]["is_last"] is True

    long_pages = quote_generator.build_layout(items(100), terms, schema, autofit=False)
    assert len(long_pages) > 2
    assert long_pages[0]["show_header"] is True
    assert long_pages[0]["show_footer"] is False
    assert all(
        not page["show_header"] and not page["show_footer"] and not page["is_last"]
        for page in long_pages[1:-1]
    )
    assert long_pages[-1]["show_header"] is False
    assert long_pages[-1]["show_footer"] is True
    assert long_pages[-1]["is_last"] is True


def assert_long_render_uses_page_roles(quote_generator) -> None:
    quote = base_quote("materials", 99)
    quote["items"] = [
        {
            "item": f"Item {i}",
            "description": "Standard material",
            "unit": "pcs",
            "qty": "1",
            "rate": "10",
        }
        for i in range(1, 101)
    ]
    html = quote_generator.render_quote_html(quote)
    sections = re.findall(r"<section\b.*?</section>", html, flags=re.DOTALL)
    assert len(sections) > 2
    assert 'data-page-role="first"' in sections[0]
    assert all('data-page-role="continuation"' in section for section in sections[1:-1])
    assert 'data-page-role="last"' in sections[-1]
    assert '<header class="esh-header"' in sections[0]
    assert all('<header class="esh-header"' not in section for section in sections[1:])
    for section in sections[:-1]:
        assert "Terms &amp; Conditions" not in section
        assert "Authorized Signatory" not in section
        assert "esh-amount-block" not in section
        assert '<footer class="esh-footer"' not in section
    assert "Terms &amp; Conditions" in sections[-1]
    assert "Authorized Signatory" in sections[-1]
    assert "esh-amount-block" in sections[-1]
    assert '<footer class="esh-footer"' in sections[-1]
    assert html.count('<div class="esh-page-number"') == len(sections)


def assert_terms_deletion_is_honoured(quote_generator) -> None:
    """Deleting terms must actually remove them from the document.

    An EMPTY terms list is a deliberate "no terms", NOT "unset" - the old
    `quote.get("terms") or _terms_for(...) or schema["terms"]` chain treated
    "the user deleted every term" as falsy and silently restored the defaults,
    so deleting terms did nothing to the PDF. An ABSENT key must still default.
    """
    defaults = quote_generator._schema_for_doc(base_quote("materials", 1))["terms"]
    assert defaults, "fixture needs schema terms to make this test meaningful"

    # 1. Deleted every term -> no terms, and no orphan heading.
    emptied = base_quote("materials", 1)
    emptied["terms"] = []
    html = quote_generator.render_quote_html(emptied)
    assert "Terms &amp; Conditions" not in html and "Terms & Conditions" not in html, \
        "emptied terms still rendered the Terms & Conditions block"
    for term in defaults:
        assert term not in html, f"deleted terms silently restored the default {term!r}"
    assert "Authorized Signatory" in html, "signature must survive an empty terms block"

    # 2. Absent key -> schema defaults (backwards compatible).
    absent = base_quote("materials", 1)
    absent.pop("terms", None)
    html = quote_generator.render_quote_html(absent)
    assert defaults[0] in html, "an absent terms key must still fall back to the defaults"

    # 3. A subset survives exactly as given.
    subset = base_quote("materials", 1)
    subset["terms"] = ["Only this one"]
    html = quote_generator.render_quote_html(subset)
    assert "Only this one" in html
    for term in defaults:
        assert term not in html, "a user term list must not be merged with the defaults"


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "eshraqat-quote-rendering"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)

    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")

        quote_generator, config, brand_assets = reload_quote_stack()
        sig_dir = config.ASSETS_DIR / "sigs"
        sig_dir.mkdir(parents=True, exist_ok=True)
        (sig_dir / "sig_1.png").write_bytes(base64.b64decode(PNG_DATA_URI.split(",", 1)[1]))

        material = base_quote("materials", 1)
        material["columns"] = [
            {"key": "item", "head": "Saved Item Header", "kind": "text", "width": 20.0},
            {"key": "description", "head": "Saved Description", "kind": "text", "width": 22.0},
            {"key": "qty", "head": "Saved Qty", "kind": "num", "width": 8.0},
            {"key": "rate", "head": "Saved Rate", "kind": "money", "width": 14.0},
            {"key": "total", "head": "Saved Total", "kind": "computed", "width": 14.0},
            {"key": "image", "head": "Saved Image", "kind": "image", "width": 18.4},
        ]
        material["col_widths"] = {
            "item": 18.0,
            "description": 24.0,
            "qty": 8.0,
            "rate": 14.0,
            "total": 14.0,
            "image": 18.4,
        }

        # Eshraqat header shows the document type "Quotation" for EVERY sub-type
        # (no Materials/Equipment/… label). The sub-type still drives the columns
        # (expected_head), just not the title.
        cases = [
            (material, "Quotation", "Saved Item Header"),
            (base_quote("equipment", 2), "Quotation", "Vehicle/Equipment Type"),
            (base_quote("manpower", 3), "Quotation", "Role Description"),
            (base_quote("service", 4), "Quotation", "Description"),
        ]
        exact_logo = brand_assets.logo_data_uri(required=True)
        for quote, title, expected_head in cases:
            html = quote_generator.render_quote_html(quote, inline_css=True)
            for expected in (
                "Eshraqat Albadr",
                "for Contracting, General Services,",
                "Heavy Equipment Rental, and Employment of Iraqi Labor",
                "www.eshraqatalbadr.com",
                "info@eshraqatalbadr.com",
                "eshraqat.albadr@gmail.com",
                title,
                quote["quote_no"],
                quote["date"],
                quote["rfq_no"],
                quote["client_name"],
                # Eshraqat quotations are always addressed to the Admin Manager
                # (user ask) - the document's own attention_to is not rendered.
                "Admin Manager",
                quote["client_address"],
                expected_head,
                quote["terms"][0],
                "Authorized Signatory",
                "sig-hand",
            ):
                assert expected in html, f"{quote['quote_type']} quote is missing {expected!r}"
            assert exact_logo in html
            assert PNG_DATA_URI in html if quote["quote_type"] in {"materials", "equipment"} else True
            assert "ABRAJ" not in html.upper()
            assert "quote_styles.css" not in html
            assert ".esh-header" in html

        material_html = quote_generator.render_quote_html(material)
        assert "width: 18.0%;" in material_html
        assert "Twenty Five Dollars and No Cents" in material_html
        assert "$25.00" in material_html
        equipment_html = quote_generator.render_quote_html(base_quote("equipment", 2))
        assert "esh-table--autofit" in equipment_html

        assert_layout_roles(quote_generator)
        assert_long_render_uses_page_roles(quote_generator)
        assert_terms_deletion_is_honoured(quote_generator)

        manager.activate("abraj")
        quote_generator, _, _ = reload_quote_stack()
        abraj_quote = base_quote("materials", 500)
        abraj_quote["quote_no"] = "QUT-MT-CLIENT-500"
        abraj_quote["signing_overlay"] = {}
        abraj_html = quote_generator.render_quote_html(abraj_quote)
        for marker in ('body class="quote-document"', 'class="q-header"', 'class="q-table', "ABRAJ ALBADR CO."):
            assert marker in abraj_html
        assert "esh-document" not in abraj_html
        assert "Eshraqat Albadr" not in abraj_html
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            reload_quote_stack()

    print("Eshraqat quotation rendering tests passed")


if __name__ == "__main__":
    main()
