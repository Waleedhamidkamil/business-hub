import importlib
import shutil
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from backend import profile_manager
from backend.profile_manager import ProfileManager


def main() -> None:
    original_manager = profile_manager._MANAGER
    root = APP_ROOT / ".test-work" / "eshraqat-template-suite"
    data_root = root / "data"
    root.mkdir(parents=True, exist_ok=True)
    if data_root.exists():
        shutil.rmtree(data_root)
    try:
        manager = ProfileManager(data_root, APP_ROOT / "static")
        profile_manager._MANAGER = manager
        manager.create_registry("eshraqat")
        manager.ensure_seed_assets("eshraqat")

        from backend import brand_assets, config, eshraqat_render

        importlib.reload(config)
        importlib.reload(brand_assets)
        importlib.reload(eshraqat_render)
        context = eshraqat_render.common_context(
            title="Quotation",
            doc_no="QT-0001",
            pages=3,
        )
        assert context["eshraqat"] is True
        assert context["brand"] == "#861432"
        assert context["logo_data_uri"].startswith("data:image/png;base64,")
        assert context["iso_data_uri"].startswith("data:image/png;base64,")

        env = Environment(
            loader=FileSystemLoader(str(APP_ROOT / "templates")),
            autoescape=select_autoescape(["html"]),
        )
        template = env.from_string(
            """
            {% import 'eshraqat/_components.html' as esh with context %}
            {{ esh.header() }}
            {{ esh.meta_grid(meta_rows) }}
            {{ esh.footer() }}
            {{ esh.page_number(1, page_count) }}
            """
        )
        html = template.render(
            **context,
            meta_rows=[
                {"label": "Quotation No.", "value": "QT-0001"},
                {"label": "Date", "value": "14/07/2026"},
            ],
        )
        for expected in (
            "Eshraqat Albadr",
            "for Contracting, General Services,",
            "Heavy Equipment Rental, and Employment of Iraqi Labor",
            "Quotation",
            "QT-0001",
            "www.eshraqatalbadr.com",
            "info@eshraqatalbadr.com",
            "eshraqat.albadr@gmail.com",
            "Page 1 of 3",
        ):
            assert expected in html
        assert "data:image/png;base64," in html
        assert "ABRAJ" not in html.upper()

        css = (APP_ROOT / "static" / "eshraqat_print.css").read_text(encoding="utf-8")
        for marker in ("@page", ".esh-header", ".esh-meta", ".esh-table", ".esh-footer"):
            assert marker in css
    finally:
        profile_manager._MANAGER = original_manager
        if "backend.config" in sys.modules:
            importlib.reload(sys.modules["backend.config"])

    print("Eshraqat shared template tests passed")


if __name__ == "__main__":
    main()
