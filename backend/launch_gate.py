"""The launch card shown while the app starts (2026-10-20; replaced the doors).

A card appears in the middle of the screen a fraction of a second after
Business Hub is launched, over whatever is on the desktop: the company logo on
a white panel, and on the company-coloured panel a greeting, the date, two rows
about the day (this month's tally and the one most pressing thing) and a
loading bar with the step in progress written under it. When the dashboard has
painted, the card fades and the app window fades in under it.

Waleed picked this (concept C of the launch-screen proposal) on 2026-10-20,
with the logo panel white, the "same text showing up animation", only the card
on screen (the desktop stays visible behind it), and one rule above the rest:
the card must never slow the start-up down.

Why Windows draws this, not the page
------------------------------------
The page cannot show anything until WebView2 has started and loaded it,
1.3-2 s after the click. The card is a plain Win32 layered window painted from
bitmaps, so it is on screen a fraction of a second after the click. Visual
Studio's splash is built this way for the same reason.

Why the app window is invisible while it loads
----------------------------------------------
WebView2 only finishes starting while its window is SHOWN, so the window
cannot be hidden until it is ready - that delays everything. Shown normally,
the half-built window appears round the card at ~1.4 s. So `hold_window`
keeps it SHOWN but invisible: Opacity 0 from its very first frame. WebView2
loads and paints underneath; nobody sees the window, and clicks go through it.
`reveal_window` fades it in under the card as the card fades. A safety timer
reveals it whatever happens. (Why not a window region, which paints faster:
see the measurements above `hold_window`. The first build covered the screen
with a backdrop in the page's colours instead; Waleed wanted only the card.)

Why a separate PROCESS
----------------------
The card animates and works out its own rows while the app starts, without
competing for the app's interpreter lock. The app talks to it through one pipe
(`GateProcess`); if the app dies, the pipe closes and the card goes with it.
The child runs below normal priority, so on a busy PC the app gets the CPU.

The card's rows cost the app NOTHING: the child reads them itself, read-only,
with `gate_brief.card()` (~0.2 s of imports + ~50 ms of queries, measured on
the live data), and they rise in at ~0.5 s - long before the dashboard.

Rules this module keeps
-----------------------
* Never blocks or breaks start-up. Any failure means no card, never a stuck
  one; a safety timer closes it if "ready" never comes.
* Only real progress. Stage 1 = company data open, 2 = window created,
  3 = page loaded, 4 = dashboard painted (`Api.gate_ready`). Between stages the
  bar eases towards the next mark but never reaches it.
* Colour is never the only signal (Waleed is colour blind): the step is
  written under the bar.
* Switching company and lock still use the page's doors (static/gate.js);
  only the start-up changed.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

DESIGN_VERSION = "card-2"

STAGES = ("Opening company data", "Starting the window",
          "Loading the interface", "Preparing your dashboard")

HOLD_MS = 200          # the bar reaches the end before the card goes - same as static/gate.js
APPEAR_MS = 180        # the card fading in
CARD_OUT_MS = 180      # the card fading out (and drifting up a little)
WINDOW_IN_MS = 200     # the app window fading in under it, after HOLD_MS
SAFETY_S = 45.0        # the card goes anyway if "ready" never comes
WINDOW_SAFETY_S = 20.0  # ...and the app window is never left invisible longer than this

WHITE = (255, 255, 255)

# The card, in u (= client height / 1040, the unit the page uses too).
# 2026-10-20, second pass: Waleed found the first size too large - about
# three quarters of it now, ~660 x 250 px on his 1080p screen.
C = {
    "w": 660, "logo_w": 210, "radius": 14, "logo_pad": 24,
    "pad_x": 34, "pad_top": 30, "pad_bottom": 26,
    "shadow_blur": 22, "shadow_dy": 10, "shadow_alpha": 80,
    "hello_px": 23, "date_px": 12.5, "date_gap": 5,
    "rows_gap": 20, "label_px": 9.5, "label_track": 0.12, "label_gap": 4,
    "value_px": 13.5, "row_gap": 12,
    "bar_gap": 22, "bar_h": 4, "step_gap": 8, "step_px": 11, "count_px": 10,
    "rise": 6, "drift": 5,
}
# "Last edited" was dropped the same day: Waleed found it useless.
ROWS = (("tally", "THIS MONTH"), ("agenda", "AGENDA"))
SEP = " · "                      # a middle dot, as gate_brief writes it
ELLIPSIS = "…"

_gate: "GateProcess | None" = None
_notes: dict[str, float] = {}


# ======================================================================
#  The app's side: start the child, talk to it
# ======================================================================
def current() -> "GateProcess | None":
    return _gate


def note(name: str) -> None:
    """Record a start-up moment (seconds since the app process began)."""
    _notes[name] = round(_seconds_since_process_start(), 3)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _user_name(app_dir: Path) -> str:
    """The name the dashboard greets, read from the one place it is written."""
    try:
        html = (app_dir / "templates" / "index.html").read_text(encoding="utf-8")
        m = re.search(r'id="dashUser">([^<]+)<', html)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except OSError:
        pass
    return "Waleed"


def gate_info(app_dir: Path) -> dict | None:
    """Everything the card needs to appear, read cheaply. None means: no card
    this time. Runs in the card's own process, so the app never waits for it."""
    try:
        from . import profile_manager
    except ImportError:                    # run as the child script, not as backend.launch_gate
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        from backend import profile_manager
    mgr = profile_manager.manager()
    try:
        pid = mgr.active_profile_id()
    except Exception:
        return None                       # first run, no registry yet
    label = mgr.PROFILE_LABELS.get(pid)
    logo = mgr.assets_dir(pid) / "logo.png"
    if not label or not logo.exists():
        return None
    # (no theme: the card looks the same in light and dark - it sits on the desktop)
    return {"profile": pid, "label": label, "accent": mgr.PROFILE_ACCENTS[pid],
            "logo": str(logo), "logo_mtime": logo.stat().st_mtime_ns,
            "user": _user_name(app_dir),
            "font": str(app_dir / "static" / "fonts" / "Roboto-VariableFont.ttf")}


def greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def date_line(now: datetime, label: str) -> str:
    """'Tuesday 20 October 2026 · Abraj Albadr' - the page's gate writes the
    date the same way (static/gate.js dateText)."""
    return f"{now:%A} {now.day} {now:%B %Y}{SEP}{label}"


class GateProcess:
    """The app's handle on the card's process. Every method is fire-and-forget:
    a card that has gone away must never stop the app."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._lock = threading.Lock()

    def alive(self) -> bool:
        """False once the card has finished - or decided not to show at all."""
        return self.proc.poll() is None

    def _send(self, **msg) -> None:
        with self._lock:
            try:
                self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
                self.proc.stdin.flush()
            except (OSError, ValueError, AttributeError):
                pass

    def stage_done(self, n: int) -> None:
        self._send(cmd="stage", n=int(n))

    def add_marks(self, marks: dict) -> None:
        self._send(cmd="marks", data={**{f"app_{k}": v for k, v in _notes.items()}, **(marks or {})})

    def open(self) -> None:
        """The dashboard has painted: finish the bar, then fade away."""
        self._send(cmd="open")

    def close(self) -> None:
        """Go now, no animation (start-up failed and an error is coming)."""
        self._send(cmd="close")


# ---------------------------------------------------------------- the app window
# How the window is kept invisible while it loads: Opacity 0, set at
# pywebview's `before_show` (on the window's own thread, just before its first
# Show()), faded back to 1 under the card as the card leaves. WinForms drops
# the layered style itself at Opacity 1, so the session is an ordinary window.
# It is what pywebview itself does for a hidden window.
#
# Measured 2026-10-20 - and why NOT a window region, though it paints faster:
#   not hidden        booted->painted 0.18 s, ready 2.25 s  (half-built window shows)
#   Opacity 0         0.28 s / 2.32 s   a LAYERED window: WebView2 paints ~0.08 s slower
#   empty region      0.22 s / 2.27 s   BUT: set before Show() the window is never
#                     MAXIMIZED (IsZoomed False); set after Show() WebView2's picture
#                     is composited 31 px LOW (title bar + border) and stays there
#                     after the region is cleared - the window rects, client area and
#                     page viewport all read correct; only the screen is wrong.
#                     Waleed saw the white strip and the cut-off bottom.
# Measured by screenshot, 3 runs each: Opacity only puts the page exactly where
# no hold does (first row y=43); any region puts it at y=74. Do not bring the
# region back without that check.
_held = None                      # the WinForms form while it is invisible
_held_lock = threading.Lock()


def _on_window_thread(form, fn) -> None:
    from System import Action                     # pythonnet, loaded by pywebview
    form.Invoke(Action(fn))


def hold_window(window) -> bool:
    """Keep the app window invisible while it loads, under the card. Only
    when the card is really up - with no card, the window shows as usual.
    Returns True when it will hold."""
    gate = _gate
    if gate is None or not gate.alive():
        return False

    # The parameter MUST be called `window`: pywebview's Event hands the window
    # only to a handler with a parameter of that name, and calls any other
    # with no arguments (the TypeError is logged and swallowed - the first
    # build named it `win`, and the window quietly showed half-built).
    def before_show(window):
        global _held
        try:
            window.native.Opacity = 0.0             # invisible from the very first frame
            with _held_lock:
                _held = window.native
            note("window_held")                     # in the start-up timeline
        except Exception as e:                      # never keep the window from showing
            print(f"[gate] window not held: {e}", file=sys.stderr)
    window.events.before_show += before_show
    # whatever happens on the page, the window is never left invisible
    t = threading.Timer(WINDOW_SAFETY_S, reveal_window)
    t.daemon = True
    t.start()
    return True


def reveal_window(delay_ms: int = 0) -> None:
    """Fade the held window in (WINDOW_IN_MS) after `delay_ms`, on its own
    thread - under the card as it fades. Safe to call any number of times,
    from any thread; does nothing when nothing is held."""
    global _held
    with _held_lock:
        form, _held = _held, None
    if form is None:
        return

    def run():
        try:
            time.sleep(max(0, delay_ms) / 1000)
            steps = 8
            for i in range(1, steps):
                k = TEXT_EASE(i / steps)
                _on_window_thread(form, lambda v=k: setattr(form, "Opacity", min(v, 0.99)))
                time.sleep(WINDOW_IN_MS / 1000 / steps)
            # 1.0 exactly: WinForms then drops the layered style itself
            _on_window_thread(form, lambda: setattr(form, "Opacity", 1.0))
        except Exception as e:                      # last resort: show it from here
            print(f"[gate] window reveal failed: {e}", file=sys.stderr)
            try:
                form.Opacity = 1.0
            except Exception:
                pass
    threading.Thread(target=run, name="reveal-window", daemon=True).start()


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def start(app_dir: Path) -> "GateProcess | None":
    """Put the card up now, in its own process. Returns None (and the app opens
    without a card) on any problem, or when DOCUMENTS_HUB_NO_GATE=1."""
    global _gate
    if os.name != "nt" or os.environ.get("DOCUMENTS_HUB_NO_GATE", "").strip() == "1":
        return None
    try:
        # Spawn first, think later: the child reads the company, logo, theme
        # and its rows itself, in parallel with the app's own start-up.
        proc = subprocess.Popen(
            [_pythonw(), str(Path(__file__).resolve()), "--child", str(Path(app_dir).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # no console; below-normal priority from the first instant, so on a
            # busy 4-thread PC the app's own start-up always gets the CPU first
            creationflags=0x08000000 | 0x4000,
            close_fds=True)
        _gate = GateProcess(proc)
        _gate._send(cmd="hello", launched_at=time.time() - _seconds_since_process_start())
        return _gate
    except Exception as e:
        print(f"[gate] not shown: {e}", file=sys.stderr)
        _gate = None
        return None


# ---------------------------------------------------------------- timings
def _seconds_since_process_start() -> float:
    """How long this process has existed, so timings include Python's own start."""
    try:
        class FT(ctypes.Structure):
            _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]
        c, e, k, u = FT(), FT(), FT(), FT()
        ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(-1), ctypes.byref(c), ctypes.byref(e),
                                               ctypes.byref(k), ctypes.byref(u))
        now = FT()
        ctypes.windll.kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))
        ticks = ((now.hi << 32) | now.lo) - ((c.hi << 32) | c.lo)
        return max(0.0, ticks / 1e7)
    except Exception:
        return 0.0


def _diagnostics_file() -> Path:
    root = os.environ.get("LOCALAPPDATA", "").strip()
    return (Path(root) if root else Path.home()) / "BusinessHub" / "Diagnostics" / "gate-timeline.json"


def page_marks(marks) -> dict:
    """The page's performance.now() moments, as seconds since the app began."""
    try:
        launched = time.time() - _seconds_since_process_start()
        origin = float(marks["origin"]) / 1000.0
        return {f"page_{k}": round(origin + float(v) / 1000.0 - launched, 3)
                for k, v in marks.items() if k != "origin" and v is not None}
    except Exception:
        return {}


def record_ready_without_gate(page: dict | None = None) -> None:
    """With no card, still note when the dashboard was ready, so a with/without
    comparison measures the same moment."""
    try:
        out = _diagnostics_file()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "gate": False,
                                   "seconds_since_launch": {**_notes, **(page or {}),
                                                            "ready": round(_seconds_since_process_start(), 3)}},
                                  indent=1), encoding="utf-8")
    except OSError:
        pass


# ======================================================================
#  The child's side: draw and animate the card
# ======================================================================
def _bezier(x1, y1, x2, y2):
    """CSS cubic-bezier(x1, y1, x2, y2) as a function of progress."""
    def at(t, a, b):
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t * t + t ** 3

    def ease(x):
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(28):
            mid = (lo + hi) / 2
            if at(mid, x1, x2) < x:
                lo = mid
            else:
                hi = mid
        return at((lo + hi) / 2, y1, y2)
    return ease


TEXT_EASE = _bezier(0.23, 1, 0.32, 1)      # words rising in - the page's --ease-out


def _mix(a, b, k):
    return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))


def fit_text(measure, parts: list[str], width: float, sep: str = SEP) -> str:
    """The most of `parts` that fits `width`, joined with `sep`: whole parts
    are dropped from the end first, then the last one left is shortened with an
    ellipsis. `measure(text)` gives a width in the same unit."""
    parts = [p for p in parts if p]
    while len(parts) > 1 and measure(sep.join(parts)) > width:
        parts.pop()
    text = sep.join(parts)
    if not text or measure(text) <= width:
        return text
    while len(text) > 1 and measure(text + ELLIPSIS) > width:
        text = text[:-1]
    return text.rstrip() + ELLIPSIS


def row_parts(key: str, data: dict) -> tuple[str, list[str]]:
    """(bold lead, [parts]) for one row from `gate_brief.card()`'s answer.
    An absent or broken part gives ("", []) and the row stays empty."""
    v = data.get(key)
    if key == "tally" and isinstance(v, dict) and v.get("text"):
        return "", [str(v["text"])]
    if key == "agenda" and isinstance(v, dict) and v.get("verb"):
        rest = [p for p in (str(v.get("text") or ""), str(v.get("more") or "")) if p]
        return str(v["verb"]), rest
    return "", []


class _Painter:
    """Pillow drawing of the card (imported lazily - Pillow costs ~0.3 s).

    Coordinates are layer pixels: the card plus a transparent margin for its
    shadow. The card is centred on the CLIENT area (below the app window's
    title bar), slightly above the middle, like the page centres things."""

    def __init__(self, info: dict, w: int, h: int, cap: int):
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        self.Image, self.ImageDraw, self.ImageFilter, self.ImageFont = Image, ImageDraw, ImageFilter, ImageFont
        self.info = info
        self.accent = _hex(info["accent"])
        self._fonts: dict = {}
        self._text: dict = {}
        g = card_geometry(w, h, cap)
        self.u, self.m, self.cw, self.ch = g["u"], g["margin"], g["card_w"], g["card_h"]
        self.lw, self.lh, self.ys = g["layer_w"], g["layer_h"], g["y"]
        self.x0 = self.m + g["text_x"]
        self.iw = g["inner_w"]

    def px(self, v: float) -> int:
        return int(round(v * self.u))

    def font(self, size: float, weight: str = "Regular"):
        key = (self.px(size), weight)
        if key not in self._fonts:
            f = self.ImageFont.truetype(self.info["font"], max(8, key[0]))
            try:
                f.set_variation_by_name(weight)
            except Exception:
                pass
            self._fonts[key] = f
        return self._fonts[key]

    def measure(self, size: float, weight: str = "Regular", tracking: float = 0.0):
        f = self.font(size, weight)
        track = tracking * size * self.u
        return lambda text: sum(f.getlength(ch) for ch in text) + track * max(0, len(text) - 1) \
            if track else f.getlength(text)

    def text_layer(self, text: str, size: float, weight: str, color=WHITE, alpha: int = 255,
                   tracking: float = 0.0):
        """A tight RGBA image of one line of text, cached. The transparent
        pixels carry the text's own colour, so the soft edges never darken
        when the line is laid over the coloured panel."""
        key = (text, size, weight, color, alpha, tracking)
        if key in self._text:
            return self._text[key]
        f = self.font(size, weight)
        asc, desc = f.getmetrics()
        width = int(self.measure(size, weight, tracking)(text)) + 2
        img = self.Image.new("RGBA", (max(1, width), asc + desc + 2), tuple(color) + (0,))
        d = self.ImageDraw.Draw(img)
        if tracking:
            x, track = 0.0, tracking * size * self.u
            for ch in text:
                d.text((x, 0), ch, font=f, fill=tuple(color) + (alpha,))
                x += f.getlength(ch) + track
        else:
            d.text((0, 0), text, font=f, fill=tuple(color) + (alpha,))
        self._text[key] = img
        return img

    def chrome(self):
        """The card without any words: shadow, white logo panel, company panel.
        The same every day (cached on disk by the child)."""
        Image, Draw = self.Image, self.ImageDraw
        m, cw, ch = self.m, self.cw, self.ch
        r = self.px(C["radius"])
        ss = 3                                          # supersampled, for smooth corners
        mask = Image.new("L", (cw * ss, ch * ss), 0)
        Draw.Draw(mask).rounded_rectangle([0, 0, cw * ss - 1, ch * ss - 1], r * ss, fill=255)
        mask = mask.resize((cw, ch), Image.LANCZOS)

        layer = Image.new("RGBA", (self.lw, self.lh), (0, 0, 0, 0))
        sh = Image.new("L", (self.lw, self.lh), 0)
        sh.paste(mask.point(lambda a: a * C["shadow_alpha"] // 255), (m, m + self.px(C["shadow_dy"])))
        sh = sh.filter(self.ImageFilter.GaussianBlur(max(1, self.px(C["shadow_blur"]) // 2)))
        layer.putalpha(sh)

        lw = self.px(C["logo_w"])
        body = Image.new("RGB", (cw, ch), WHITE)
        # the company panel: its colour, a little deeper towards the bottom
        top, bottom = _mix(self.accent, WHITE, 0.04), _mix(self.accent, (0, 0, 0), 0.24)
        col = Image.new("RGB", (1, 256))
        col.putdata([_mix(top, bottom, i / 255) for i in range(256)])
        body.paste(col.resize((cw - lw, ch), Image.BILINEAR), (lw, 0))
        # the logo, as large as the white panel allows
        logo = Image.open(self.info["logo"]).convert("RGBA")
        pad = self.px(C["logo_pad"])
        logo.thumbnail((lw - 2 * pad, ch - 2 * pad), Image.LANCZOS)
        body.paste(logo, ((lw - logo.width) // 2, (ch - logo.height) // 2), logo)
        card = body.convert("RGBA")
        card.putalpha(mask)
        layer.alpha_composite(card, (m, m))
        # a hairline round the card, so the white panel holds its edge over a
        # light window behind it
        ring = Image.new("L", (cw * ss, ch * ss), 0)
        d = Draw.Draw(ring)
        d.rounded_rectangle([0, 0, cw * ss - 1, ch * ss - 1], r * ss, fill=255)
        d.rounded_rectangle([ss, ss, cw * ss - 1 - ss, ch * ss - 1 - ss], max(0, r - 1) * ss, fill=0)
        ring = ring.resize((cw, ch), Image.LANCZOS).point(lambda a: a * 22 // 255)
        edge = Image.new("RGBA", (cw, ch), (15, 23, 42, 0))
        edge.putalpha(ring)
        layer.alpha_composite(edge, (m, m))
        return layer

    def words(self, now: datetime) -> dict:
        """The greeting and the date line, as text layers."""
        return {"hello": self.text_layer(f"{greeting(now.hour)}, {self.info['user']}",
                                         C["hello_px"], "Bold"),
                "date": self.text_layer(date_line(now, self.info["label"]), C["date_px"], "Regular",
                                        alpha=190)}

    def row_layer(self, key: str, label: str, data: dict):
        """(label image, value image) for one row, or None when it has nothing."""
        lead, parts = row_parts(key, data)
        if not lead and not parts:
            return None
        lab = self.text_layer(label, C["label_px"], "Medium", alpha=150, tracking=C["label_track"])
        bold = self.text_layer(lead + " ", C["value_px"], "Bold") if lead else None
        room = self.iw - (bold.width if bold else 0)
        rest = fit_text(self.measure(C["value_px"]), parts, room)
        img_rest = self.text_layer(rest, C["value_px"], "Regular", alpha=225) if rest else None
        w = (bold.width if bold else 0) + (img_rest.width if img_rest else 0)
        h = max(i.height for i in (bold, img_rest) if i is not None)
        val = self.Image.new("RGBA", (max(1, w), h), WHITE + (0,))
        x = 0
        for part in (bold, img_rest):
            if part is not None:
                val.alpha_composite(part, (x, 0))
                x += part.width
        return lab, val


def card_geometry(w: int, h: int, cap: int) -> dict:
    """Where everything goes, in layer pixels. Pure arithmetic (no Pillow), so
    the child can place its windows before Pillow has loaded."""
    u = (h - cap) / 1040.0

    def px(v):
        return int(round(v * u))
    y = {}
    yy = px(C["pad_top"])
    y["hello"] = yy
    yy += px(C["hello_px"] * 1.25) + px(C["date_gap"])
    y["date"] = yy
    yy += px(C["date_px"] * 1.4) + px(C["rows_gap"])
    y["rows"] = yy
    for i, _ in enumerate(ROWS):
        y[f"label{i}"] = yy
        yy += px(C["label_px"] * 1.4) + px(C["label_gap"])
        y[f"value{i}"] = yy
        yy += px(C["value_px"] * 1.35) + (px(C["row_gap"]) if i < len(ROWS) - 1 else 0)
    yy += px(C["bar_gap"])
    y["bar"] = yy
    yy += px(C["bar_h"]) + px(C["step_gap"])
    y["step"] = yy
    yy += px(C["step_px"] * 1.4) + px(C["pad_bottom"])
    card_w, card_h = px(C["w"]), yy
    # room for the shadow: the blur reaches ~3 sigma (sigma = blur / 2) past
    # the card, and it sits shadow_dy lower
    margin = px(C["shadow_blur"]) * 3 // 2 + px(C["shadow_dy"]) + 4
    text_x = px(C["logo_w"]) + px(C["pad_x"])
    # y positions are card-local above; the layer adds the margin
    y = {k: v + margin for k, v in y.items()}
    return {"u": u, "margin": margin, "card_w": card_w, "card_h": card_h,
            "layer_w": card_w + 2 * margin, "layer_h": card_h + 2 * margin,
            "text_x": text_x, "inner_w": card_w - text_x - px(C["pad_x"]), "y": y}


class _Anim:
    """Opacity + rise for one element, eased like the CSS."""

    def __init__(self, start: float, dur: float = 0.36, rise: int = 0):
        self.start, self.dur, self.rise = start, dur, rise

    def at(self, now: float) -> tuple[float, int]:
        p = TEXT_EASE(min(1.0, max(0.0, (now - self.start) / self.dur)))
        return p, int(round(self.rise * (1 - p)))

    def done(self, now: float) -> bool:
        return now >= self.start + self.dur


# ---- Win32 (child only) ----
if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:
        dwmapi = None

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
                    ("hIconSm", wintypes.HICON)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class ULWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hdcDst", wintypes.HDC),
                    ("pptDst", ctypes.POINTER(wintypes.POINT)), ("psize", ctypes.POINTER(wintypes.SIZE)),
                    ("hdcSrc", wintypes.HDC), ("pptSrc", ctypes.POINTER(wintypes.POINT)),
                    ("crKey", wintypes.COLORREF), ("pblend", ctypes.POINTER(BLENDFUNCTION)),
                    ("dwFlags", wintypes.DWORD), ("prcDirty", ctypes.POINTER(wintypes.RECT))]

    def _sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)

    _sig(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
    _sig(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
         wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    _sig(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    _sig(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
    _sig(user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
    _sig(user32.UpdateLayeredWindow, wintypes.BOOL, wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
         ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
         ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
    _sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
    _sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
    _sig(user32.PeekMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
         wintypes.UINT, wintypes.UINT)
    _sig(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
    _sig(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
    _sig(user32.SystemParametersInfoW, wintypes.BOOL, wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT)
    _sig(user32.LoadCursorW, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID)
    _sig(user32.SetProcessDPIAware, wintypes.BOOL)
    _sig(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
    _sig(user32.MonitorFromPoint, wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
    _sig(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
    _sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    _sig(user32.UpdateLayeredWindowIndirect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(ULWINFO))
    _sig(kernel32.SetPriorityClass, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD)
    _sig(kernel32.GetCurrentProcess, wintypes.HANDLE)
    _sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
    _sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
    _sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
    _sig(gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER),
         wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    _sig(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
    _sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    if dwmapi is not None:
        _sig(dwmapi.DwmFlush, ctypes.c_long)

    WS_POPUP = 0x80000000
    WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x80000, 0x80, 0x8, 0x08000000
    WM_MOUSEACTIVATE, MA_NOACTIVATE = 0x0021, 3
    SW_SHOWNOACTIVATE = 4
    ULW_ALPHA = 2
    IDC_APPSTARTING = 32650
    SPI_GETWORKAREA = 0x0030
    SM_CYCAPTION = 4
    BELOW_NORMAL_PRIORITY_CLASS, NORMAL_PRIORITY_CLASS, ABOVE_NORMAL_PRIORITY_CLASS = 0x4000, 0x20, 0x8000


def _priority(cls: int) -> None:
    """While the app loads, the card yields the CPU to it (Waleed's PC has 4
    threads and is often busy); for the fade-out it asks for it back."""
    try:
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), cls)
    except Exception:
        pass


def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_MOUSEACTIVATE:
        return MA_NOACTIVATE           # a click on the card never steals focus
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_REF = None
_CLASS = "BusinessHubLaunchGate"


class _Layer:
    """One layered window showing one premultiplied BGRA bitmap.

    WS_EX_TOOLWINDOW matters beyond the taskbar: Chromium's window-occlusion
    check ignores tool windows, so the app's page keeps rendering under the
    card instead of pausing as if hidden."""

    def __init__(self, x: int, y: int, w: int, h: int):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.hwnd = user32.CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            _CLASS, "", WS_POPUP, x, y, w, h, None, None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error())
        self.screen = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.screen)
        bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self.bits = ctypes.c_void_p()
        self.hbmp = gdi32.CreateDIBSection(self.screen, ctypes.byref(bih), 0, ctypes.byref(self.bits), None, 0)
        if not self.hbmp:
            raise OSError(ctypes.get_last_error())
        self.old = gdi32.SelectObject(self.mem, self.hbmp)
        self.shown = False

    def load(self, bgra: bytes, row: int = 0):
        """Copy rows of pixels in, starting at `row` (full-width rows)."""
        offset = row * self.w * 4
        n = min(len(bgra), self.w * self.h * 4 - offset)
        if n > 0:
            ctypes.memmove(self.bits.value + offset, bgra, n)

    def present(self, alpha: int = 255):
        """Hand the whole bitmap to Windows (and show the window the first time)."""
        dst = wintypes.POINT(self.x, self.y)
        size = wintypes.SIZE(self.w, self.h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
        user32.UpdateLayeredWindow(self.hwnd, self.screen, ctypes.byref(dst), ctypes.byref(size),
                                   self.mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
        if not self.shown:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.shown = True

    def fade(self, alpha: int, dy: int = 0):
        """Change only the opacity (and nudge the position): no source DC, so
        Windows reuses the picture it already has instead of copying it again,
        which keeps every fade frame nearly free."""
        if not self.shown:
            self.present(alpha)
            return
        dst = wintypes.POINT(self.x, self.y + int(dy))
        blend = BLENDFUNCTION(0, 0, max(0, min(255, int(alpha))), 1)
        user32.UpdateLayeredWindow(self.hwnd, self.screen, ctypes.byref(dst), None,
                                   None, None, 0, ctypes.byref(blend), ULW_ALPHA)

    def present_rows(self, y0: int, y1: int):
        """Show changed rows only - the rest of the card is not re-copied."""
        if not self.shown:
            self.present()
            return
        dst = wintypes.POINT(self.x, self.y)
        size = wintypes.SIZE(self.w, self.h)
        src = wintypes.POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)
        dirty = wintypes.RECT(0, max(0, y0), self.w, min(self.h, y1))
        info = ULWINFO(ctypes.sizeof(ULWINFO), self.screen, ctypes.pointer(dst), ctypes.pointer(size),
                       self.mem, ctypes.pointer(src), 0, ctypes.pointer(blend), ULW_ALPHA,
                       ctypes.pointer(dirty))
        if not user32.UpdateLayeredWindowIndirect(self.hwnd, ctypes.byref(info)):
            self.present()

    def destroy(self):
        try:
            user32.DestroyWindow(self.hwnd)
            gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteObject(self.hbmp)
            gdi32.DeleteDC(self.mem)
            user32.ReleaseDC(None, self.screen)
        except Exception:
            pass


def _work_area():
    """The work area of the monitor under the mouse: pywebview centres the app
    window with WinForms' CenterScreen, which picks that monitor."""
    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        mon = user32.MonitorFromPoint(pt, 2)          # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return mi.rcWork
    area = wintypes.RECT()
    user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(area), 0)
    return area


def _bgra(img) -> bytes:
    """Premultiplied BGRA, as UpdateLayeredWindow wants it."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img.convert("RGBa").tobytes("raw", "BGRa")


def _cache_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA", "").strip()
    return (Path(root) if root else Path.home()) / "BusinessHub" / "gate"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _sweep_cache(keep: set[Path]) -> None:
    """Old pictures are no use: the doors' (before 2026-10-20) at once, the
    card's own after two days."""
    try:
        for old in _cache_dir().glob("*.bgra"):
            if old in keep:
                continue
            stale = not old.name.startswith("card-") or time.time() - old.stat().st_mtime > 2 * 86400
            if stale:
                old.unlink()
    except OSError:
        pass


class _ChildGate:
    def __init__(self, info: dict):
        self.info = info
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.timeline: dict[str, float] = {}
        self.stage = 0
        self.bar = 0.0
        self.label = STAGES[0]
        self.prev_label: str | None = None
        self.label_at = -10.0
        self.stage_at = time.perf_counter()
        self.card: dict | None = None

    def _mark(self, name):
        self.timeline[name] = round(time.time() - self.info.get("launched_at", time.time()), 3)

    def _reader(self):
        try:
            for raw in sys.stdin.buffer:
                try:
                    self.q.put(json.loads(raw.decode("utf-8")))
                except ValueError:
                    continue
        except Exception:
            pass
        self.q.put({"cmd": "eof"})             # the app has gone: close with it

    def _load_card(self):
        """The card's rows, worked out here, in the card's own process: the
        app is never asked and never waits. Read-only."""
        try:
            from backend import gate_brief
            data = gate_brief.card()
        except Exception as e:                 # noqa: BLE001 - the rows just stay empty
            data = {"ok": False, "error": repr(e)[:300]}
        self.q.put({"cmd": "card", "data": data})

    @staticmethod
    def _pump():
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _goal(self) -> float:
        """Where the bar is heading: the finished stages' mark, then most of
        the way - never all of it - towards the next one."""
        mark = self.stage / 4.0
        if self.stage >= 4:
            return 1.0
        waited = time.perf_counter() - self.stage_at
        return mark + 0.6 * (0.25) * (1 - pow(2.718, -waited / 0.9))

    def _set_stage(self, n: int):
        n = max(self.stage, min(4, n))
        if n == self.stage:
            return
        self.stage = n
        self.stage_at = time.perf_counter()
        new = "Ready" if n >= 4 else STAGES[n]
        if new != self.label:
            self.prev_label, self.label, self.label_at = self.label, new, time.perf_counter()

    def run(self):
        global _WNDPROC_REF
        layers: list[_Layer] = []
        try:
            user32.SetProcessDPIAware()
            _WNDPROC_REF = WNDPROC(_wndproc)
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = _WNDPROC_REF
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_APPSTARTING))
            wc.lpszClassName = _CLASS
            user32.RegisterClassExW(ctypes.byref(wc))
            area = _work_area()
            W, H = area.right - area.left, area.bottom - area.top
            cap = user32.GetSystemMetrics(SM_CYCAPTION)
            threading.Thread(target=self._reader, daemon=True).start()

            # Only the card: the desktop stays visible round it, and the app
            # window loads invisibly underneath (hold_window, in the app).
            g = card_geometry(W, H, cap)
            cx = area.left + W // 2
            cy = area.top + cap + int((H - cap) * 0.47)
            card = _Layer(cx - g["layer_w"] // 2, cy - g["layer_h"] // 2, g["layer_w"], g["layer_h"])
            layers.append(card)

            # Two pictures are kept between launches, so the card is on screen
            # with no drawing at all - Pillow alone costs ~0.3 s, and on a busy
            # PC a screen that has to draw first showed ~2 s late (filmed
            # 2026-10-02). `chrome` is the card with no words (the same every
            # day); `first` is today's opening picture with the greeting, the
            # date and the empty bar.
            key = hashlib.sha1("|".join(str(v) for v in (
                DESIGN_VERSION, self.info["profile"], self.info["accent"], self.info["label"],
                self.info["logo_mtime"], W, H, cap)).encode()).hexdigest()[:16]
            now_dt = datetime.now()
            day_key = hashlib.sha1(f"{key}|{now_dt:%Y-%m-%d}|{greeting(now_dt.hour)}|{self.info['user']}"
                                   .encode()).hexdigest()[:16]
            cache_chrome = _cache_dir() / f"card-chrome-{key}.bgra"
            cache_first = _cache_dir() / f"card-first-{day_key}.bgra"
            chrome_raw = first_raw = None
            try:
                chrome_raw = cache_chrome.read_bytes()
                first_raw = cache_first.read_bytes()
            except OSError:
                pass
            size = g["layer_w"] * g["layer_h"] * 4
            chrome_raw = chrome_raw if chrome_raw and len(chrome_raw) == size else None
            first_raw = first_raw if first_raw and len(first_raw) == size else None
            if chrome_raw:
                card.load(first_raw or chrome_raw)
                self._appear([card])                 # no drawing at all
            self.timeline["chrome_cached"] = 1 if chrome_raw else 0
            self.timeline["words_cached"] = 1 if first_raw else 0

            # The rows: worked out in this process, in parallel with drawing.
            # Started now, not when the app window appears: deferring it was
            # measured (2026-10-20, 5 launches) - ready did not move (2.26 vs
            # 2.27 s), the work just competed with the page load instead, and
            # the rows came at ~1.95 s instead of ~0.6 s.
            threading.Thread(target=self._load_card, daemon=True).start()

            p = _Painter(self.info, W, H, cap)
            if chrome_raw:
                chrome = p.Image.frombytes("RGBa", (p.lw, p.lh), chrome_raw, "raw", "BGRa").convert("RGBA")
            else:
                chrome = p.chrome()
                card.load(_bgra(chrome))
                try:
                    _write_atomic(cache_chrome, _bgra(chrome))
                except OSError:
                    pass
                self._appear([card])
            self._mark("drawn")

            words = p.words(now_dt)
            ys, x0, rise = p.ys, p.x0, p.px(C["rise"])
            t0 = -100.0 if first_raw else time.perf_counter()     # cached words are already in place
            # each piece: (image, x, y, animation); baked into `still` once it has landed
            pieces = {"hello": (words["hello"], x0, ys["hello"], _Anim(t0, rise=rise)),
                      "date": (words["date"], x0, ys["date"], _Anim(t0 + 0.07, rise=rise))}
            bar_anim = _Anim(t0 + 0.14, rise=rise)
            still = chrome.copy()                    # everything that has stopped moving
            # The next launch's opening picture: the greeting and date on the
            # chrome. Never the rows - they are worked out fresh every time.
            opening = chrome.copy()
            first_saved = bool(first_raw)

            deadline = time.monotonic() + SAFETY_S
            last = time.perf_counter()
            open_at = None
            drawn_fw, bar_drawn_at = -1, 0.0
            self._mark("ready_to_draw")
            while True:
                self._pump()
                try:
                    msg = self.q.get(timeout=0.004)
                except queue.Empty:
                    msg = None
                if msg:
                    cmd = msg.get("cmd")
                    if cmd == "close" or (cmd == "eof" and open_at is None):
                        return                 # (once leaving, a closed pipe lets it finish)
                    if cmd == "stage" and int(msg.get("n", 0)) > self.stage:
                        self._set_stage(int(msg["n"]))
                        self._mark(f"stage{self.stage}")
                    elif cmd == "card" and self.card is None:
                        self.card = msg.get("data") or {}
                        self._mark("card_data")
                        if self.card.get("errors") or self.card.get("error"):
                            self.timeline["card_errors"] = str(self.card.get("errors") or self.card.get("error"))[:300]
                        t = time.perf_counter()
                        for i, (rkey, rlabel) in enumerate(ROWS):
                            made = p.row_layer(rkey, rlabel, self.card)
                            if made:
                                lab, val = made
                                pieces[f"label{i}"] = (lab, x0, ys[f"label{i}"], _Anim(t + 0.07 * i, rise=rise))
                                pieces[f"value{i}"] = (val, x0, ys[f"value{i}"], _Anim(t + 0.07 * i + 0.03, rise=rise))
                    elif cmd == "marks":
                        self.timeline.update(msg.get("data") or {})
                    elif cmd == "open" and open_at is None:
                        self._set_stage(4)
                        self._mark("ready")
                        open_at = time.perf_counter() + HOLD_MS / 1000
                if open_at is None and time.monotonic() > deadline:
                    self._set_stage(4)
                    open_at = time.perf_counter() + HOLD_MS / 1000
                # ease the bar, then draw - only what moved, so a quiet card
                # costs no CPU the app could be using
                t = time.perf_counter()
                dt, last = t - last, t
                tau = 0.09 if self.stage >= 4 else 0.22
                self.bar += (self._goal() - self.bar) * (1 - pow(2.718, -dt / tau))
                fw = int(p.iw * max(0.0, min(1.0, self.bar)))
                spans = []
                # whatever has landed joins the still picture - drawn once more
                # at full strength, then never again
                for k in [k for k, pc in pieces.items() if pc[3].done(t)]:
                    img, x, y, _ = pieces.pop(k)
                    still.alpha_composite(img, (x, y))
                    if k in ("hello", "date"):
                        opening.alpha_composite(img, (x, y))
                    spans.append((y - rise - 2, y + img.height + rise + 2))
                moving = bool(pieces)
                for img, _, y, _ in pieces.values():
                    spans.append((y - rise - 2, y + img.height + rise + 2))
                step_moving = t - self.label_at < 0.4 or not bar_anim.done(t)
                if step_moving:
                    spans.append((ys["bar"] - rise - 4, ys["step"] + p.px(C["step_px"] * 1.6) + rise))
                if not first_saved and "hello" not in pieces and "date" not in pieces:
                    self._save_first(p, opening, cache_first)
                    first_saved = True
                if spans:
                    y0 = max(0, min(a for a, _ in spans))
                    y1 = min(p.lh, max(b for _, b in spans))
                    frame = self._frame(p, still, pieces, bar_anim, (y0, y1), t)
                    card.load(_bgra(frame), row=y0)
                    card.present_rows(y0, y1)
                    drawn_fw = fw
                elif fw != drawn_fw and t - bar_drawn_at >= 1 / 30:
                    # only the bar moved: redraw just its strip, at most 30 times a second
                    y0, y1 = ys["bar"] - 2, ys["bar"] + p.px(C["bar_h"]) + 2
                    frame = self._frame(p, still, {}, bar_anim, (y0, y1), t)
                    card.load(_bgra(frame), row=y0)
                    card.present_rows(y0, y1)
                    drawn_fw, bar_drawn_at = fw, t
                # Leave on time even if a row is still rising: the app is
                # ready, and the card never holds it back.
                if open_at is not None and t >= open_at:
                    break
                if (moving or step_moving) and dwmapi is not None:
                    dwmapi.DwmFlush()
            self.timeline["gate_cpu_s_before_open"] = round(time.process_time(), 3)
            _priority(ABOVE_NORMAL_PRIORITY_CLASS)    # the app is ready; the fade must be smooth
            self._leave(p, card)
            self._mark("opened")
            self.timeline["gate_cpu_s"] = round(time.process_time(), 3)
            self._save_timeline()
            _sweep_cache({cache_chrome, cache_first})
        except Exception as e:                       # never take the app down
            try:
                _diagnostics_file().with_name("gate-error.txt").write_text(
                    f"{datetime.now():%Y-%m-%d %H:%M:%S} {e!r}", encoding="utf-8")
            except OSError:
                pass
        finally:
            for layer in reversed(layers):
                layer.destroy()
            self._pump()

    def _appear(self, layers: list[_Layer]):
        """Fade in. The full picture is handed over once, then only the
        opacity changes."""
        self._mark("shown" if "shown" not in self.timeline else "card_shown")
        for layer in layers:
            layer.present(alpha=0)
        t0 = time.perf_counter()
        while True:
            k = min(1.0, (time.perf_counter() - t0) / (APPEAR_MS / 1000))
            a = int(255 * TEXT_EASE(k))
            for layer in layers:
                layer.fade(a)
            self._pump()
            if k >= 1:
                break
            if dwmapi is not None:
                dwmapi.DwmFlush()

    def _leave(self, p: "_Painter", card: _Layer):
        """The card fades and drifts up a little, while the app window fades
        in under it (reveal_window, in the app, on the same HOLD_MS)."""
        drift = p.px(C["drift"])
        t0 = time.perf_counter()
        while True:
            el = time.perf_counter() - t0
            kc = TEXT_EASE(min(1.0, el / (CARD_OUT_MS / 1000)))
            card.fade(int(255 * (1 - kc)), dy=-int(drift * kc))
            self._pump()
            if el >= CARD_OUT_MS / 1000:
                break
            if dwmapi is not None:
                dwmapi.DwmFlush()

    def _save_first(self, p, still, path: Path):
        """Keep today's opening picture (nothing finished yet) for the next launch."""
        try:
            frame = still.copy()
            self._draw_bar(p, frame, 0.0, STAGES[0], None, 1.0, 0, -10.0, time.perf_counter(), 0)
            _write_atomic(path, _bgra(frame))
        except OSError:
            pass

    def _frame(self, p, still, pieces, bar_anim, rows, t):
        """Rows y0..y1 of the card at time t: the still picture, the pieces
        still moving, the bar and the step under it."""
        y0, y1 = rows
        img = still.crop((0, y0, p.lw, y1))
        for img_piece, x, y, anim in pieces.values():
            k, dy = anim.at(t)
            self._put(img, img_piece, x, y - y0 + dy, k)
        k, dy = bar_anim.at(t)
        self._draw_bar(p, img, self.bar, self.label, self.prev_label, k, dy, self.label_at, t, y0)
        return img

    @staticmethod
    def _put(img, layer, x, y, alpha):
        if alpha <= 0.004 or y >= img.height or y + layer.height <= 0:
            return
        src = layer
        if y < 0 or y + layer.height > img.height:
            top, bottom = max(0, -y), min(layer.height, img.height - y)
            src = layer.crop((0, top, layer.width, bottom))
            y += top
        if alpha < 0.996:
            src = src.copy()
            src.putalpha(src.getchannel("A").point(lambda v: int(v * alpha)))
        img.alpha_composite(src, (int(x), int(y)))

    def _draw_bar(self, p, img, bar, label, prev_label, k, dy, label_at, t, top):
        """The bar (white on the company colour, its track a faint white) and
        the step written under it - colour is never the only signal."""
        if k <= 0.004:
            return
        x0, iw = p.x0, p.iw
        by = p.ys["bar"] - top + dy
        bh = max(2, p.px(C["bar_h"]))
        a = k
        # Drawn on its own small picture and laid over: ImageDraw on an RGBA
        # image SETS pixels rather than blending, so a faint track drawn
        # straight onto the card punched a see-through hole in it.
        strip = p.Image.new("RGBA", (iw + 1, bh + 1), WHITE + (0,))
        d = p.ImageDraw.Draw(strip)
        d.rounded_rectangle([0, 0, iw, bh], bh // 2, fill=WHITE + (56,))
        fw = int(iw * max(0.0, min(1.0, bar)))
        if fw >= bh:
            d.rounded_rectangle([0, 0, fw, bh], bh // 2, fill=WHITE + (255,))
        self._put(img, strip, x0, by, a)
        sy = p.ys["step"] - top + dy
        since = t - label_at
        if prev_label and since < 0.18:
            f = 1 - TEXT_EASE(since / 0.18)
            old = p.text_layer(prev_label, C["step_px"], "Medium", alpha=235)
            self._put(img, old, x0, sy - int(p.px(4) * (1 - f)), f * a)
        fin = TEXT_EASE(min(1.0, max(0.0, (since - 0.06) / 0.26))) if prev_label else 1.0
        cur = p.text_layer(label, C["step_px"], "Medium", alpha=235)
        self._put(img, cur, x0, sy + int(p.px(4) * (1 - fin)), fin * a)
        n = min(STAGES.index(label) + 1, 4) if label in STAGES else 4
        count = p.text_layer(f"Step {n} of 4" if label in STAGES else "Done",
                             C["count_px"], "Regular", alpha=160)
        self._put(img, count, x0 + iw - count.width, sy + p.px(1), a)

    def _save_timeline(self):
        try:
            out = _diagnostics_file()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                       "design": DESIGN_VERSION,
                                       "seconds_since_launch": self.timeline}, indent=1), encoding="utf-8")
        except OSError:
            pass


def _child_main() -> int:
    # argv: --child <app dir>. The first line on stdin says when the app was
    # launched (for the timeline); then commands.
    app_dir = Path(sys.argv[sys.argv.index("--child") + 1])
    first = sys.stdin.buffer.readline()
    try:
        launched_at = float(json.loads(first.decode("utf-8"))["launched_at"])
    except (ValueError, KeyError, TypeError):
        launched_at = time.time()
    try:
        info = gate_info(app_dir)
    except Exception:
        info = None
    if not info:
        return 0                            # no company yet (first run): no card
    info["launched_at"] = launched_at
    _ChildGate(info).run()
    return 0


if __name__ == "__main__" and "--child" in sys.argv:
    # os._exit, NOT sys.exit (2026-10-07). The daemon `_reader` thread is still
    # blocked in sys.stdin.buffer - the app keeps the pipe open - so normal
    # interpreter shutdown cannot take the stdin lock to close it and aborts
    # ("Fatal Python error: _enter_buffered_busy ... possibly due to daemon
    # threads"): the "Just-In-Time Debugger: unhandled win32 exception in
    # pythonw.exe" box after every start-up from 2026-10-02 to 10-07. Everything
    # the child keeps is already written (timeline, cache) and its windows are
    # destroyed, so there is nothing for shutdown to do. app.py exits the same
    # way for a similar reason. Pinned by tests/gate_child_exit.test.py.
    code = _child_main()
    try:
        sys.stderr and sys.stderr.flush()
    except (OSError, ValueError):
        pass
    os._exit(code)
