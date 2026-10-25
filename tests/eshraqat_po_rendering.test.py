import importlib
import json
import shutil
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def _reload_renderer():
    from backend import brand_assets, config, eshraqat_render, pdf_generator, signing

    for module in (config, brand_assets, eshraqat_render, signing, pdf_generator):
        importlib.reload(module)
    return config, pdf_generator


def _write_profile_settings(manager: ProfileManager, profile_id: str, root: Path) -> None:
    settings_path = manager.settings_path(profile_id)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps({"documents_root": str(root / profile_id / "documents")}),
        encoding="utf-8",
    )


def _sample_rows(count: int = 1, image: str = "") -> list[dict]:
    rows = [
        {
            "name": f"Hydraulic Pump {index}",
            "description": "Heavy-duty replacement assembly",
            "unit": "pcs",
            "qty": str(index),
            "company": "Basra Industrial Supply",
            "source_quote_no": "QT-0041",
        }
        for index in range(1, count + 1)
    ]
    if image:
        rows[0]["image"] = image
    return rows


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "eshraqat-po-rendering-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)

    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")
        _write_profile_settings(manager, "eshraqat", root)
        _write_profile_settings(manager, "abraj", root)

        sig_dir = manager.assets_dir("eshraqat") / "sigs"
        sig_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(APP_ROOT / "static" / "brands" / "eshraqat" / "logo.png", sig_dir / "sig_1.png")

        config, pdf_generator = _reload_renderer()
        assert config.ACTIVE_PROFILE == "eshraqat"

        overlay = {
            "slots": {
                "main": {
                    "sig_index": 1,
                    "stamp": False,
                    "sig_dx_mm": 0,
                    "sig_dy_mm": 0,
                    "sig_rot": 0,
                    "stamp_vs_sig_y_mm": 0,
                }
            }
        }
        html = pdf_generator.render_html(
            "PO-0041",
            "14/07/2026",
            _sample_rows(),
            inline_css=True,
            signing_overlay=overlay,
            col_widths={
                "name": 36,
                "description": 27,
                "unit": 9,
                "qty": 8,
                "company": 20,
            },
            layout={"autofit_rows": True},
        )

        # 2026-08-09 - the Eshraqat PO skin is Arabic + RTL (the only one of the
        # four that is; quote/dn/inv stay English + LTR).
        for expected in (
            '<html lang="ar" dir="rtl">',
            'class="esh-document esh-po-document rtl"',
            "شركة اشراقات البدر",
            "(مشروع ماء البحر المشترك)",
            "طلب شراء",
            "التاريخ",
            "رقم طلب الشراء",
            "PO-0041",
            "14/07/2026",
            "رقم عرض السعر",
            "QT-0041",
            # Table head, in RTL source order: ت is the RIGHTMOST column.
            '<th class="col-idx al-center">ت</th>',
            '<th class="col-name al-right">المادة</th>',
            '<th class="col-desc al-right">الوصف</th>',
            '<th class="col-unit al-center">الوحدة</th>',
            '<th class="col-qty al-center">العدد</th>',
            '<th class="col-company al-right">الشركة</th>',
            '<th class="col-price al-center">السعر</th>',
            "Basra Industrial Supply",
            "Hydraulic Pump 1",
            "Heavy-duty replacement assembly",
            "pcs",
            # Translated sign box.
            "الشركة المجهزة",
            "التوقيع المعتمد",
            # Page number replaces the (removed) footer band.
            "صفحة 1 من 1",
        ):
            assert expected in html, f"missing Eshraqat PO content: {expected}"
        for forbidden in (
            "ABRAJ", "شركة ابراج", "المدير العام", "رقم الطلب :",
            # The English chrome the redesign replaced.
            "Purchase Order No.", "Authorized Signatory", "Source Quotation",
            "Page 1 of 1",
            # The footer band is gone from the PO - contacts/ISO print nowhere.
            # (Match the ELEMENT, not the class name: the inlined print CSS
            # still carries `.esh-footer` rules for the other four skins.)
            '<footer class="esh-footer"', "www.eshraqatalbadr.com",
        ):
            assert forbidden not in html, f"Eshraqat PO still carries: {forbidden}"
        assert "esh-table items-table esh-table--autofit items-table--autofit" in html
        assert ".items-table .col-name { width:" in html
        assert 'class="sig-hand"' in html
        assert "data:image/png;base64," in html

        # The picture column: absent unless a row carries one, then it sits
        # between الشركة and السعر - the same slot as the Abraj PO - and the
        # `--po-img-mm` cap must agree with the paginator's reserve.
        assert 'class="col-image' not in html, "picture column printed on a PO with no pictures"
        # The class attribute, not the CSS: the template's inline <style> block
        # always ships the `.items-table.has-images` width overrides.
        assert ' has-images"' not in html
        img_html = pdf_generator.render_html(
            "PO-0041", "14/07/2026",
            _sample_rows(2, image="data:image/png;base64,AAAA"),
            inline_css=True,
        )
        assert '<th class="col-image al-center">صورة</th>' in img_html
        head = img_html[img_html.index("<thead>"):img_html.index("</thead>")]
        assert head.index("col-company") < head.index("col-image") < head.index("col-price")
        assert "items-table has-images" in img_html
        assert f"--po-img-mm: {round(pdf_generator.PO_IMAGE_ROW_MIN_MM, 2)}mm" in img_html

        # 2026-07-29 - a long PO paginates instead of minting extra PO numbers.
        # 60 rows is comfortably past the eshraqat single-page budget
        # (260 usable − 50 header − 46 closeout − 12 thead ≈ 152mm ≈ 21 rows).
        many = _sample_rows(60)
        pages = pdf_generator.paginate_po(many, "PO-0042")
        assert len(pages) > 1, "60 rows should need more than one page"
        # ONE number on every page - the whole point of the change.
        assert {page["po_no"] for page in pages} == {"PO-0042"}
        assert [p["page_index"] for p in pages] == list(range(1, len(pages) + 1))
        assert all(p["total_pages"] == len(pages) for p in pages)
        # Page roles: header on page 1 only, signature on the last page only.
        assert [p["show_header"] for p in pages] == [True] + [False] * (len(pages) - 1)
        assert [p["is_last"] for p in pages] == [False] * (len(pages) - 1) + [True]
        # Row numbering is continuous, and no row is lost or duplicated.
        flat = [r["index"] for p in pages for r in p["rows"]]
        assert flat == list(range(1, len(many) + 1))

        paged_html = pdf_generator.render_html("PO-0042", "14/07/2026", many)
        # The successor numbers the old splitter would have invented must be absent.
        for ghost in ("PO-0043", "PO-0044"):
            assert ghost not in paged_html, f"paginated PO leaked a split number: {ghost}"
        assert paged_html.count('data-page-role="first"') == 1
        assert paged_html.count('data-page-role="last"') == 1
        # Header + meta print on page 1 only; the closing zone on the last page
        # only; the Arabic page number on EVERY page (it is the only thing left
        # at the foot of the page now that the footer band is gone).
        assert paged_html.count('class="esh-header esh-header-ar"') == 1
        assert paged_html.count('class="esh-meta"') == 1
        assert paged_html.count('class="esh-closing-zone"') == 1
        # 2026-10-11 - render_html plans on MEASURED rows (plan_po), which packs
        # tighter than the estimate `paginate_po` uses above, so the rendered
        # page count is read from the rendered pages themselves.
        rendered = paged_html.count('class="esh-page-number"')
        assert rendered > 1, "60 rows should still need more than one page"
        assert paged_html.count("<section class=\"esh-page") == rendered
        for index in range(1, rendered + 1):
            assert f"صفحة {index} من {rendered}" in paged_html

        manager.activate("abraj")
        config, pdf_generator = _reload_renderer()
        abraj_html = pdf_generator.render_html(
            "A-7",
            "14/07/2026",
            [{"name": "Pipe", "description": "Steel", "unit": "pcs", "qty": "2", "company": "Vendor"}],
        )
        assert '<html lang="ar" dir="rtl">' in abraj_html
        assert "شركة ابراج البدر" in abraj_html
        assert "esh-document" not in abraj_html
        assert "A-7" in abraj_html
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("Eshraqat PO rendering tests passed")


if __name__ == "__main__":
    main()
