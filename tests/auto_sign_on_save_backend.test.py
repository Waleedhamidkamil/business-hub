"""Auto-sign on save (2026-07-24).

Every save signs the document with the configured default (signature 1 + stamp)
and RE-ROLLS the placement each time, while preserving an explicit per-slot pick.
Guards the two ways this could go wrong:
  • claiming "signed" when no signature/stamp image exists (badge would lie), and
  • silently discarding a signature the user chose by hand.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, signing


def set_auto(**kw):
    s = config.load_user_settings() or {}
    sec = dict(s.get("signing") or {})
    auto = dict(sec.get("auto") or {})
    auto.update(kw)
    sec["auto"] = auto
    s["signing"] = sec
    config.save_user_settings(s)


def main():
    saved = json.dumps(config.load_user_settings() or {}, ensure_ascii=False)
    sig_dir = config._sig_dir() if hasattr(config, "_sig_dir") else signing._sig_dir()
    stamp = sig_dir / signing.STAMP_FILE
    sig1 = sig_dir / signing.SIG_FILES[1]
    have_assets = stamp.exists() or sig1.exists()
    try:
        # --- Defaults: auto-sign is ON, signature 1 + stamp. -----------------
        s = signing.get_settings()
        assert s["auto"]["enabled"] is True, s["auto"]
        assert s["auto"]["sig_index"] == 1, s["auto"]
        assert s["auto"]["stamp"] is True, s["auto"]

        # --- Never claim signed when the images don't exist. -----------------
        if not have_assets:
            ov = signing.overlay_for_save("dn", None)
            assert not signing.overlay_is_signed(ov or {}), \
                "must not auto-sign when no signature/stamp image is uploaded"
            print("auto_sign_on_save_backend: passed (no signing assets installed)")
            return

        # --- Auto-sign fills an unsigned document. ---------------------------
        set_auto(enabled=True, sig_index=1, stamp=True)
        ov = signing.overlay_for_save("dn", None)
        assert signing.overlay_is_signed(ov), f"auto-sign should sign an unsigned doc: {ov}"
        # DN signs the SUPPLIER box only - the recipient box is the client's pen.
        assert set(ov["slots"].keys()) == {"supplier"}, ov["slots"]

        # --- Re-roll: same picks, different placement. -----------------------
        rolls = set()
        for _ in range(12):
            o = signing.overlay_for_save("dn", ov)
            slot = o["slots"]["supplier"]
            assert slot["sig_index"] == 1 and slot["stamp"] is True, \
                f"re-roll must preserve the picks: {slot}"
            rolls.add((slot["sig_dx_mm"], slot["sig_dy_mm"], slot["sig_rot"]))
        assert len(rolls) > 1, f"placement should re-roll every save, got {rolls}"

        # --- An explicit pick survives (stamp off, signature 2). -------------
        manual = signing.roll_overlay("dn", {"supplier": {"sig_index": 2, "stamp": False}})
        out = signing.overlay_for_save("dn", manual)
        assert out["slots"]["supplier"]["sig_index"] == 2, out
        assert out["slots"]["supplier"]["stamp"] is False, \
            "an explicitly cleared stamp must not be re-added by auto-sign"

        # --- Auto-sign OFF leaves an unsigned document alone. ----------------
        set_auto(enabled=False)
        assert signing.overlay_for_save("inv", None) is None, \
            "auto-sign off must not sign anything"
        # ...but an existing pick is still re-rolled.
        again = signing.overlay_for_save("dn", manual)
        assert again["slots"]["supplier"]["sig_index"] == 2, again

        # --- Settings round-trip exactly as the Settings card sends it. ------
        signing.save_settings({
            "signatures": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
            "size": {"stamp_w_mm": 46.5, "stamp_h_mm": 29.0,
                     "sig_w_mm": 55.0, "sig_h_mm": 22.0},
            "auto": {"enabled": True, "stamp": False, "sig_index": 3},
        })
        back = signing.get_settings()["auto"]
        assert back == {"enabled": True, "stamp": False, "sig_index": 3}, back
        # "No signature (stamp only)" sends null - must survive as None, not 0.
        signing.save_settings({"auto": {"enabled": True, "stamp": True, "sig_index": None}})
        assert signing.get_settings()["auto"]["sig_index"] is None, \
            "stamp-only must persist as sig_index=None"
        # Saving unrelated settings must not clobber the auto block.
        signing.save_settings({"size": {"stamp_w_mm": 40.0}})
        assert signing.get_settings()["auto"]["enabled"] is True, \
            "a size-only save wiped the auto block"

        print("auto_sign_on_save_backend: passed")
    finally:
        config.save_user_settings(json.loads(saved))


if __name__ == "__main__":
    main()
