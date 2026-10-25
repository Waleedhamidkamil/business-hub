import base64
import importlib
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = APP_ROOT / ".test-work" / "eshraqat-invoice-rendering"
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAusB9Y9Z4S8AAAAASUVORK5CYII="
)


def _reload_rendering(profile_id: str):
    manager = profile_manager._MANAGER
    manager.activate(profile_id)
    if profile_id == "eshraqat":
        manager.ensure_seed_assets(profile_id)
    manager.assets_dir(profile_id).mkdir(parents=True, exist_ok=True)
    manager.signatures_dir(profile_id).mkdir(parents=True, exist_ok=True)

    from backend import brand_assets, config, eshraqat_render, inv_generator, signing

    importlib.reload(config)
    importlib.reload(brand_assets)
    importlib.reload(eshraqat_render)
    importlib.reload(signing)
    return importlib.reload(inv_generator), config


def _item(inv_type: str, index: int = 1) -> dict:
    common = {
        "description": f"Rental and support line {index}",
        "unit": "Month",
        "qty": "2",
        "rate": "125.50",
    }
    if inv_type == "equipment":
        common["equipment"] = f"Crane {index}"
    elif inv_type == "manpower":
        common["role"] = f"Technician {index}"
    elif inv_type == "service":
        common["description"] = f"Inspection service {index}"
    else:
        common["item"] = f"Material {index}"
    return common


def _invoice(inv_type: str, count: int = 1, **overrides) -> dict:
    payload = {
        "inv_no": "IV-0001",
        "date": "14/07/2026",
        "inv_type": inv_type,
        "client_name": "North Field Client",
        "lpo_no": "LPO-7788",
        "client_address": "Basra, Iraq",
        "payment_bank_key": "cash",
        "withholding_included": True,
        "source_quote_no": "QT-0042",
        "paid": True,
        "items": [_item(inv_type, i) for i in range(1, count + 1)],
        "layout": {"autofit_rows": False},
        "signing_overlay": {},
    }
    payload.update(overrides)
    return payload


def _assert_shared_identity(html: str) -> None:
    for expected in (
        "Eshraqat Albadr",
        "for Contracting, General Services,",
        "Heavy Equipment Rental, and Employment of Iraqi Labor",
        "www.eshraqatalbadr.com",
        "info@eshraqatalbadr.com",
        "eshraqat.albadr@gmail.com",
    ):
        assert expected in html, expected
    for forbidden in (
        "ABRAJ ALBADR",
        "ABRAJ AL BADER",
        "Abu Dhabi Islamic Bank",
        "Bank of Baghdad",
        "Eng. Ali Majeed",
    ):
        assert forbidden.lower() not in html.lower(), forbidden


def assert_all_subtypes_use_eshraqat_shell(inv_generator) -> None:
    # Eshraqat header shows the document type "Invoice" for EVERY sub-type
    # (no Materials/Equipment/… label); the sub-type still drives columns + folder.
    expected_labels = {
        "materials": "Invoice",
        "equipment": "Invoice",
        "manpower": "Invoice",
        "service": "Invoice",
    }
    for inv_type, label in expected_labels.items():
        html = inv_generator.render_inv_html(_invoice(inv_type))
        assert '<body class="esh-document esh-invoice-document"' in html
        assert f'data-invoice-type="{inv_type}"' in html
        assert label in html
        assert "IV-0001" in html
        assert "LPO-7788" in html
        assert "QT-0042" in html
        assert "North Field Client" in html
        assert "Basra, Iraq" in html
        assert "Payment Method" in html and "Cash" in html
        assert "Withholding Tax" in html and "Included" in html
        assert "No Dollars" not in html
        assert "$251.00" in html
        assert "Paid status" not in html
        _assert_shared_identity(html)


def assert_document_columns_widths_and_autofit(inv_generator) -> None:
    columns = [
        {"key": "description", "head": "Service Scope", "kind": "text", "width": 40.0},
        {"key": "monthly_rate", "head": "Monthly Rate", "kind": "money", "width": 20.0},
        {"key": "remarks", "head": "Remarks", "kind": "text", "width": 15.0},
        {"key": "qty", "head": "Q'ty", "kind": "num", "width": 8.0},
        {"key": "total", "head": "Total", "kind": "computed", "width": 12.4},
    ]
    doc = _invoice(
        "service",
        columns=columns,
        col_widths={
            "description": 38.0,
            "monthly_rate": 22.0,
            "remarks": 15.0,
            "qty": 8.0,
            "total": 12.4,
        },
        items=[{
            "description": "Monthly inspection",
            "monthly_rate": "750",
            "remarks": "Night shift",
            "qty": "1",
            "rate": "750",
        }],
        layout={"autofit_rows": True},
    )
    html = inv_generator.render_inv_html(doc)
    for value in ("Service Scope", "Monthly Rate", "Remarks", "Night shift"):
        assert value in html
    for width in ("38.0%", "22.0%", "15.0%", "8.0%", "12.4%"):
        assert f"width: {width}" in html
    assert '<table class="esh-table esh-table--autofit"' in html

    fixed_html = inv_generator.render_inv_html(
        _invoice("materials", layout={"autofit_rows": False})
    )
    assert '<table class="esh-table esh-table--autofit"' not in fixed_html
    assert "--esh-row-mm:" in fixed_html


def assert_withholding_and_payment_context(inv_generator, config) -> None:
    excluded = inv_generator.render_inv_html(
        _invoice("materials", withholding_included=False)
    )
    assert "Withholding Tax" in excluded
    assert "Not Included" in excluded

    original_banks = list(config.INV_BANK_INFO)
    try:
        config.INV_BANK_INFO = [{
            "key": "esh-future",
            "label": "Eshraqat Future Bank",
            "company_line": "ESHRAQAT ALBADR",
            "bank_name": "Mesopotamia Business Bank",
            "branch": "Baghdad",
            "swift": "MBIBIQBA",
            "account_no": "44001234",
            "iban": "IQ00MBIB44001234",
            "currency": "USD",
        }]
        bank_html = inv_generator.render_inv_html(
            _invoice("materials", payment_bank_key="esh-future")
        )
    finally:
        config.INV_BANK_INFO = original_banks

    for expected in (
        "Payment Info",
        "ESHRAQAT ALBADR",
        "Mesopotamia Business Bank",
        "MBIBIQBA",
        "44001234",
        "IQ00MBIB44001234",
        "USD",
    ):
        assert expected in bank_html
    for forbidden in ("Abu Dhabi Islamic Bank", "Bank of Baghdad", "19616543"):
        assert forbidden not in bank_html


def assert_page_roles_and_signing(inv_generator, config) -> None:
    sig_dir = config.ASSETS_DIR / "sigs"
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / "sig_1.png").write_bytes(TINY_PNG)
    (sig_dir / "stamp.png").write_bytes(TINY_PNG)
    overlay = {
        "slots": {
            "main": {
                "sig_index": 1,
                "stamp": True,
                "stamp_dx_mm": 0,
                "stamp_dy_mm": 0,
                "stamp_rot": 0,
                "sig_dx_mm": 0,
                "sig_dy_mm": 0,
                "sig_rot": 0,
                "stamp_vs_sig_y_mm": 0,
            }
        }
    }

    single = inv_generator.render_inv_html(_invoice("materials", 1))
    assert single.count('data-page-role="single"') == 1
    assert single.count("Page 1 of 1") == 1

    two_page = inv_generator.render_inv_html(
        _invoice("equipment", 12, signing_overlay=overlay)
    )
    assert two_page.count('<section class="esh-page') == 2
    assert two_page.count('data-page-role="header"') == 1
    assert two_page.count('data-page-role="footer"') == 1
    assert 'data-page-role="body"' not in two_page
    assert two_page.count("esh-header") >= 1
    assert two_page.count("esh-footer") >= 1
    assert two_page.count('class="sig-overlay"') == 1
    assert two_page.count('class="sig-hand"') == 1
    assert two_page.count('class="sig-stamp"') == 1
    assert "Page 1 of 2" in two_page and "Page 2 of 2" in two_page

    long_html = inv_generator.render_inv_html(_invoice("manpower", 40))
    assert long_html.count('<section class="esh-page') >= 3
    assert long_html.count('data-page-role="header"') == 1
    assert long_html.count('data-page-role="body"') >= 1
    assert long_html.count('data-page-role="footer"') == 1
    assert long_html.count("esh-document-title") == 1
    # Totals now live in the quotation-style amount block (Subtotal/Withholding/
    # Grand Total), only on the footer page - the old in-table esh-amount-total
    # row is gone.
    assert long_html.count("esh-grand-total") == 1
    assert long_html.count("esh-amount-block") == 1
    assert long_html.count("esh-footer-contact") == 1


def assert_inline_css_and_abraj_regression(manager) -> None:
    inv_generator, _ = _reload_rendering("eshraqat")
    inline_html = inv_generator.render_inv_html(_invoice("materials"), inline_css=True)
    assert "@page" in inline_html
    assert ".esh-header" in inline_html
    assert "inv_styles.css" not in inline_html

    manager.assets_dir("abraj").mkdir(parents=True, exist_ok=True)
    inv_generator, _ = _reload_rendering("abraj")
    abraj = inv_generator.render_inv_html({
        **_invoice("materials"),
        "inv_no": "INV-MT-CLIENT-001",
        "signing_overlay": {},
    })
    assert '<body class="inv-document"' in abraj
    assert "ABRAJ ALBADR CO." in abraj
    assert "esh-invoice-document" not in abraj
    assert "eshraqat_print.css" not in abraj


def main() -> None:
    original_manager = profile_manager._MANAGER
    if WORK_ROOT.exists():
        shutil.rmtree(WORK_ROOT)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    manager = ProfileManager(WORK_ROOT / "data", APP_ROOT / "static")
    try:
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        inv_generator, config = _reload_rendering("eshraqat")

        assert_all_subtypes_use_eshraqat_shell(inv_generator)
        assert_document_columns_widths_and_autofit(inv_generator)
        assert_withholding_and_payment_context(inv_generator, config)
        assert_page_roles_and_signing(inv_generator, config)
        assert_inline_css_and_abraj_regression(manager)
    finally:
        profile_manager._MANAGER = original_manager
        for module_name in (
            "backend.config",
            "backend.brand_assets",
            "backend.eshraqat_render",
            "backend.signing",
            "backend.inv_generator",
        ):
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])

    print("Eshraqat invoice rendering tests passed")


if __name__ == "__main__":
    main()
