import importlib
import re
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def _reload_dn_stack():
    from backend import brand_assets, config, eshraqat_render, signing

    importlib.reload(config)
    importlib.reload(brand_assets)
    importlib.reload(eshraqat_render)
    importlib.reload(signing)

    from backend import dn_generator

    return importlib.reload(dn_generator)


def _dn(**overrides):
    value = {
        "dn_no": "DN-0042",
        "date": "14/07/2026",
        "client_name": "Mesopotamia Engineering",
        "lpo_no": "LPO-7788",
        "client_address": "Basra Logistics Yard",
        "source_quote_no": "QT-0031",
        "include_prices": False,
        "items": [
            {
                "item": "Pipe",
                "description": "Carbon steel pipe",
                "unit": "pcs",
                "qty": "2",
                "rate": "7.5",
            },
            {
                "item": "Valve",
                "description": "Gate valve",
                "unit": "pcs",
                "qty": "3",
                "rate": "10",
            },
        ],
        "layout": {"autofit_rows": False},
        "signing_overlay": {"slots": {}},
    }
    value.update(overrides)
    return value


def _page_roles(html: str) -> list[str]:
    return re.findall(r'data-page-role="([^"]+)"', html)


def _header_width(html: str, heading: str) -> float:
    match = re.search(
        rf'<th[^>]*style="width:\s*([0-9.]+)%[^>]*>\s*{re.escape(heading)}\s*</th>',
        html,
        flags=re.IGNORECASE,
    )
    if not match:
        raise AssertionError(f"missing rendered width for {heading!r}")
    return float(match.group(1))


def assert_hidden_prices_and_metadata(dn_generator) -> None:
    html = dn_generator.render_dn_html(_dn())

    assert 'data-document-family="eshraqat"' in html
    assert 'data-document-type="dn"' in html
    assert "Eshraqat Albadr" in html
    assert "www.eshraqatalbadr.com" in html
    assert "info@eshraqatalbadr.com" in html
    assert "ABRAJ" not in html.upper()
    for expected in (
        "Delivery Note",
        "DN-0042",
        "14/07/2026",
        "Mesopotamia Engineering",
        "LPO-7788",
        "Basra Logistics Yard",
        "QT-0031",
        # "Linked Quotation" since 2026-10-02: one header layout and one set of
        # labels for quotation, delivery note and invoice (eshraqat_render.meta_rows).
        "Linked Quotation",
    ):
        assert expected in html

    assert re.search(r">\s*Rate\s*</th>", html, flags=re.IGNORECASE) is None
    assert re.search(r">\s*Total\s*</th>", html, flags=re.IGNORECASE) is None
    assert '<tr class="esh-dn-grand-row">' not in html
    assert "Recipient Signature" in html
    # Supplier signature removed - DN keeps only the recipient signature.
    assert "Supplier Signature" not in html
    assert 'data-signing-slot="recipient-manual"' in html
    assert 'data-signing-slot="supplier"' not in html
    assert re.search(
        r"\.esh-dn-closing-zone\s*\{[^}]*position:\s*absolute",
        html,
        flags=re.DOTALL,
    )


def assert_shown_prices_and_totals(dn_generator) -> None:
    html = dn_generator.render_dn_html(_dn(include_prices=True))

    assert re.search(r">\s*Rate\s*</th>", html, flags=re.IGNORECASE)
    assert re.search(r">\s*Total\s*</th>", html, flags=re.IGNORECASE)
    assert '<tr class="esh-dn-grand-row">' in html
    assert "$15.00" in html
    assert "$30.00" in html
    assert "$45.00" in html
    assert "Forty Five Dollars and No Cents" in html


def assert_custom_widths_and_autofit(dn_generator) -> None:
    html = dn_generator.render_dn_html(
        _dn(
            columns=[
                {"key": "item", "head": "Item", "kind": "text", "width": 30.0},
                {
                    "key": "description",
                    "head": "Description",
                    "kind": "text",
                    "width": 45.0,
                },
                {
                    "key": "remarks",
                    "head": "Remarks",
                    "kind": "text",
                    "width": 20.4,
                },
            ],
            col_widths={"item": 25.0, "description": 50.0, "remarks": 20.4},
            layout={"autofit_rows": True},
        )
    )

    assert "esh-table--autofit" in html
    assert "Remarks" in html
    assert _header_width(html, "NO.") == 4.6     # "No" until 2026-10-02 - see dn_generator
    assert _header_width(html, "Item") == 25.0
    assert _header_width(html, "Description") == 50.0
    assert _header_width(html, "Remarks") == 20.4


def assert_multi_page_roles(dn_generator) -> None:
    items = [
        {
            "item": f"Item {index}",
            "description": "Delivered material",
            "unit": "pcs",
            "qty": "1",
        }
        for index in range(1, 81)
    ]
    html = dn_generator.render_dn_html(_dn(items=items))
    roles = _page_roles(html)

    assert len(roles) >= 3
    assert roles[0] == "header"
    assert roles[-1] == "footer"
    assert set(roles[1:-1]) == {"body"}
    assert html.count("class=\"esh-header\"") == 1
    assert html.count("class=\"esh-footer\"") == 1
    assert html.count("class=\"esh-page-number\"") == len(roles)
    assert f"Page 1 of {len(roles)}" in html
    assert f"Page {len(roles)} of {len(roles)}" in html


def assert_abraj_renderer_is_unchanged(manager: ProfileManager) -> None:
    manager.activate("abraj")
    dn_generator = _reload_dn_stack()
    html = dn_generator.render_dn_html(_dn(dn_no="DN-ABRAJ-CHECK"))

    assert 'class="dn-document"' in html
    assert 'class="d-header"' in html
    assert 'data-document-family="eshraqat"' not in html
    assert 'class="esh-header"' not in html


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "eshraqat-dn-rendering-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)

    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")

        dn_generator = _reload_dn_stack()
        assert_hidden_prices_and_metadata(dn_generator)
        assert_shown_prices_and_totals(dn_generator)
        assert_custom_widths_and_autofit(dn_generator)
        assert_multi_page_roles(dn_generator)
        assert_abraj_renderer_is_unchanged(manager)
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("Eshraqat delivery-note rendering tests passed")


if __name__ == "__main__":
    main()
