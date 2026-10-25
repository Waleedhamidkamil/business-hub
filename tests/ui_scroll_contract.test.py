"""Regression contract for constrained To-Do and PO-link flex scrollers."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "app_styles.css").read_text(encoding="utf-8")


def declarations(selector: str) -> str:
    matches = re.findall(rf"{re.escape(selector)}\s*\{{([^}}]*)\}}", CSS, re.S)
    assert matches, f"missing CSS rule: {selector}"
    return " ".join(re.sub(r"\s+", " ", body).strip().lower() for body in matches)


def assert_decl(selector: str, declaration: str) -> None:
    body = declarations(selector)
    assert declaration.lower() in body, f"{selector} must include {declaration}; got {body}"


def main() -> None:
    # 2026-10-21: the To-Do is one page of Insights tables. The page is the
    # ONE scroller; no table scrolls on its own (nested scrollers fought over
    # the wheel). tests/todo_table.test.js checks the tables have no cap.
    assert_decl(".todo-body", "overflow-y: auto")
    assert_decl(".todo-body", "min-height: 0")

    assert_decl(".po-link-list", "overflow: auto")
    assert_decl(".po-link-section", "flex: 0 0 auto")
    assert_decl(".po-link-section-rows", "flex: 0 0 auto")
    assert_decl(".po-link-row", "flex: 0 0 auto")
    assert_decl(".po-link-row", "height: auto")
    assert_decl(".po-link-row", "white-space: normal")

    print("ui scroll contract tests passed")


if __name__ == "__main__":
    main()
