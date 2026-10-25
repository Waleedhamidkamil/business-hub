"""Dashboard/costing payloads must always be valid strict JSON."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import database  # noqa: E402
from backend.api import _bridge_json_safe  # noqa: E402


def assert_image_column_is_not_parsed_as_money() -> None:
    row = {
        "equipment": "Hydraulic Crane",
        "col_2": "data:image/jpeg;base64," + ("9876543210" * 1000),
        "col_3": "EA",
        "col_4": "1",
        "col_5": "600",
    }
    econ = database._line_econ(row, "equipment")
    assert econ["revenue"] == 600.0
    assert math.isfinite(econ["line_profit"])


def assert_non_finite_values_are_bridge_safe() -> None:
    dirty = {
        "revenue": float("inf"),
        "rows": [{"profit": float("-inf"), "margin": float("nan")}],
        "normal": 12.5,
    }
    clean = _bridge_json_safe(dirty)
    assert clean == {
        "revenue": 0.0,
        "rows": [{"profit": 0.0, "margin": 0.0}],
        "normal": 12.5,
    }
    json.dumps(clean, allow_nan=False)
    assert database.cost_to_usd(float("inf"), "USD") == 0.0


def main() -> None:
    assert_image_column_is_not_parsed_as_money()
    assert_non_finite_values_are_bridge_safe()
    print("dashboard JSON safety backend tests passed")


if __name__ == "__main__":
    main()
