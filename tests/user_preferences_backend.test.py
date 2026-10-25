import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config


def main():
    original_path = config.SETTINGS_PATH
    original_preferences = copy.deepcopy(config.USER_PREFERENCES)

    settings_path = Path(__file__).with_name("user_preferences_settings.fixture.json")
    previous = settings_path.read_text(encoding="utf-8")
    try:
        config.SETTINGS_PATH = settings_path
        config.save_user_settings({
            "preferences": {
                "theme": "dark",
                "after_export": "open",
                "compact_tables": True,
                "dn_prices_default": True,
            }
        })

        config.USER_PREFERENCES = copy.deepcopy(original_preferences)
        config._apply_user_settings()
        prefs = config.current_effective_settings().get("preferences")

        if not prefs:
            raise AssertionError("effective settings should expose user preferences")
        if prefs.get("theme") != "dark":
            raise AssertionError("theme preference should load from settings.json")
        if prefs.get("after_export") != "open":
            raise AssertionError("after_export preference should load from settings.json")
        if prefs.get("compact_tables") is not True:
            raise AssertionError("compact_tables preference should load as a boolean")
        if prefs.get("dn_prices_default") is not True:
            raise AssertionError("dn_prices_default preference should load as a boolean")
    finally:
        config.SETTINGS_PATH = original_path
        config.USER_PREFERENCES = original_preferences
        settings_path.write_text(previous, encoding="utf-8")

    print("user preference backend tests passed")


if __name__ == "__main__":
    main()
