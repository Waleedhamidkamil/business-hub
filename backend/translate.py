"""Suggest the missing half of a bilingual item record.

This is the SECOND place in the app that talks to the internet - `image_search`
is the other - and it deliberately follows the same rules:

  * **stdlib only** (`urllib.request`). No new dependency; the app still installs
    and runs with the network unplugged.
  * **Bounded.** One connection timeout, one read timeout, a byte cap. A slow or
    hostile response cannot hang the editor.
  * **Never raises.** Every failure is a returned `{"ok": False, "error": ...}`.
    Nothing here may break a save, and nothing here is on a save path.
  * **A suggestion is OFFERED, never applied.** Every row comes back marked
    `"suggested"`. Master Data is what documents print from; a confident-looking
    wrong English name on an invoice is worse than a blank one, because nobody
    re-checks a cell that is already filled. The same reasoning as
    `image_search`'s MIN_SCORE, for the same reason.

**What leaves this machine:** item names, descriptions and units - the words
that would be printed on a document anyway - plus a glossary of the catalogue's
own existing pairs. No document number, client, price, cost, company or profile
is ever sent.

**Why an LLM and not a translation API.** A unit is not a sentence: ask Google
Translate for `عدد` and it returns "number", which is right in Arabic and wrong
on an invoice. `master_items.unit_english_map` already answers units exactly from
the catalogue's own vocabulary, and that vocabulary is put IN the prompt here so
the model uses the business's words rather than inventing synonyms.

**Configuration.** `data/settings.json` is tracked in git, so the key does NOT
live there. It comes from `data/secrets.json` (git-ignored) or the environment:

    data/secrets.json   {"translation": {"api_key": "...", "model": "..."}}
    environment         GEMINI_API_KEY / GOOGLE_API_KEY

Absent a key the module reports `configured: False` and the UI simply does not
offer the button. That is the whole of the "offline-first" contract here.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import config

# Google's endpoint. `model` is configurable because model names change faster
# than this file will; a wrong one surfaces Google's own 404 text verbatim
# rather than a guess of ours.
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# The key goes in a HEADER, never in the query string. A URL is the part of a
# request that gets logged, put in an exception's text and shown in a toast; a
# header is not. Google accepts both, so this costs nothing.
_KEY_HEADER = "x-goog-api-key"
# MEASURED, not guessed - 2026-09-10, against this account, this catalogue and
# this prompt. Eight models, three rounds each, run ROUND-ROBIN because this
# service's latency drifts minute to minute. Batch of 16 real Arabic item names
# whose English the catalogue already knows, with those pairs REMOVED from the
# glossary so a model could not copy the answer out of the prompt.
#
#   model                    median   worst   hard trade terms
#   gemini-3.5-flash          20.1s   21.2s   best, and the steadiest of all
#   gemini-3.1-flash-lite      6.7s   18.4s   fastest, but see below
#   gemini-3.5-flash-lite     17.8s   40.6s   the old default; erratic and worse
#   gemini-flash-lite-latest  14.6s   15.1s   same answers as the old default
#   gemma-4-31b-it            50.3s   51.7s   3.5-flash's answers, 2.5x slower
#   gemini-flash-latest       80.1s   81.3s   3.5-flash's answers, 4x slower
#   gemini-3.8-flash          83.3s   83.7s   3.5-flash's answers, 4x slower
#   gemini-2.5-flash-lite        -       -    404, not on this account at all
#
# The exact-match score barely separated them (8/16 vs 9/16) because most of
# the misses are the catalogue's own house wording - every model says "Frozen
# Fish Fillet" where we wrote "Frozen Fillet Fish". The IRAQI TRADE TERMS are
# what separate them, and they are the whole reason this feature exists:
#
#   كلبس كابسة  staples for a stapler
#       3.5-flash "Staples"          | 3.5-flash-lite "Wire Rope Clip"  <-- WRONG
#   بوري شخاط   rectangular box-section pipe
#       3.5-flash "Rectangular Pipe" | 3.1-flash-lite "Match Pipe"      <-- literal
#   برغي سيركت  circuit-breaker screw
#       3.5-flash "Circuit Breaker Screw" | lite "Circuit Screw"
#   خشب سير     timber batten
#       3.5-flash "Timber Batten"    | 3.1-flash-lite "Conveyor Wood"   <-- literal
#   لوبياء
#       3.5-flash "Black-Eyed Peas"  | 3.1-flash-lite "Kidney Beans"    <-- WRONG
#
# A lite model put "Wire Rope Clip" where the item is stationery. That reaches a
# client's invoice, so 13 seconds of latency is the cheaper side of the trade.
# 3.1-flash-lite leads the fallbacks because when the primary is busy a fast
# answer a human still reviews beats no answer at all.
_DEFAULT_MODEL = "gemini-3.5-flash"
_FALLBACK_MODELS = ("gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-3.8-flash")
_BUSY = (429, 500, 502, 503, 504)
_READ_TIMEOUT = 30             # covers connect AND read: urlopen's `timeout`
                               # is the socket's, for the life of the request
# Total wall clock across every attempt for ONE batch. A person is holding a
# button down; four 30s timeouts in a row is not a retry policy, it is a hang.
_TOTAL_BUDGET = 75
_MAX_BYTES = 2 * 1024 * 1024   # a translation reply is kilobytes; cap the rest
_MAX_ITEMS = 60                # ONE request; a longer list is split into several
_MAX_TOTAL = 240               # four requests; more than this is a background job
_MAX_GLOSSARY = 60

_AR = re.compile(r"[؀-ۿ]")


def _is_arabic(value: Any) -> bool:
    return bool(_AR.search(str(value or "")))


def _secrets_path() -> Path:
    """`data/secrets.json` - the APP's data root, not the profile's.

    A key belongs to this machine, not to Abraj or Eshraqat. `config.DATA_DIR`
    IS the active profile's folder, so using it would give each company its own
    separate key and make the feature appear to switch off when you switch
    company.
    """
    try:
        from . import profile_manager
        return profile_manager.shared_db_path().parent.parent / "secrets.json"
    except Exception:
        return Path(__file__).resolve().parents[1] / "data" / "secrets.json"


def _settings() -> dict[str, Any]:
    """Key + model, from the git-ignored secrets file or the environment."""
    out: dict[str, Any] = {"provider": "gemini", "model": _DEFAULT_MODEL, "api_key": ""}
    try:
        path = _secrets_path()
        if path.is_file():
            # BYTES, not text. On Windows, PowerShell's `>` and `Out-File` write
            # UTF-16 with a BOM, so a key created the obvious way is unreadable
            # as UTF-8 and the app silently reports "no key". `json.loads` on
            # bytes detects UTF-8/16/32 and the BOM itself (RFC 4627 §3).
            raw = json.loads(path.read_bytes())
            block = raw.get("translation") if isinstance(raw, dict) else None
            if isinstance(block, dict):
                for key in ("provider", "model", "api_key"):
                    if str(block.get(key) or "").strip():
                        out[key] = str(block[key]).strip()
    except (OSError, ValueError):
        pass                                     # a broken secrets file is "not configured"
    if not out["api_key"]:
        out["api_key"] = (os.environ.get("GEMINI_API_KEY")
                          or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return out


def status() -> dict[str, Any]:
    """Whether suggestions can be offered at all. Never touches the network."""
    s = _settings()
    configured = bool(s["api_key"])
    return {
        "ok": True,
        "configured": configured,
        "provider": s["provider"],
        "model": s["model"],
        "reason": "" if configured else
                  "No API key. Put it in data/secrets.json under "
                  '{"translation": {"api_key": "..."}} or set GEMINI_API_KEY.',
    }


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #

def _glossary(items: list[dict[str, Any]], limit: int = _MAX_GLOSSARY,
              want: str = "en") -> list[tuple[str, str]]:
    """Existing pairs from THIS catalogue, most relevant first, ASKED side first.

    A pair that shares a word with something being translated is worth far more
    than a random one: `طاولة دائرية` is solved by having seen `طاولة`. So the
    word-overlap matches lead, and the rest fill the budget. The overlap is
    scored against the side being ASKED about, which flips with the direction
    (2026-09-10) - scoring an English query against Arabic names finds nothing.
    """
    try:
        from . import master_items
        conn = master_items._connect()
        try:
            master_items.init(conn)
            rows = conn.execute(
                "SELECT name_ar, name_en FROM master_items "
                "WHERE TRIM(COALESCE(name_ar,'')) <> '' AND TRIM(COALESCE(name_en,'')) <> ''"
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []

    wanted: set[str] = set()
    for item in items:
        for word in re.split(r"\s+", str(item.get("name") or "")):
            if len(word) > 2:
                wanted.add(word)

    to_arabic = str(want or "en").lower().startswith("ar")
    scored: list[tuple[int, str, str]] = []
    for r in rows:
        ar, en = str(r["name_ar"]).strip(), str(r["name_en"]).strip()
        if not ar or not en:
            continue
        asked, answer = (en, ar) if to_arabic else (ar, en)
        hit = sum(1 for w in wanted if w and w.lower() in asked.lower())
        scored.append((hit, asked, answer))
    scored.sort(key=lambda t: -t[0])
    return [(asked, answer) for _hit, asked, answer in scored[:limit]]


def _build_prompt(items: list[dict[str, Any]], units: dict[str, str],
                  glossary: list[tuple[str, str]], want: str = "en") -> str:
    """The prompt, in whichever direction is being asked (2026-09-10).

    A purchase order is written in Arabic and needs its English half. A
    QUOTATION is written in English and needs its Arabic half, so the same
    goods are found when they are later ordered. Same rules, same shape, same
    JSON contract - only the language named in it changes, because two prompts
    would drift the way two implementations always do.
    """
    to_arabic = str(want or "en").lower().startswith("ar")
    W = "ar" if to_arabic else "en"
    wanted_lang = "Arabic" if to_arabic else "English"
    other_lang = "English" if to_arabic else "Arabic"
    lines = [
        "You are naming procurement line items for an Iraqi contracting and "
        "supply company. Purchase orders are written in Arabic; quotations, "
        "delivery notes and invoices go to clients in English.",
        "",
        f"Give the best concise {wanted_lang} trade name for every item, using "
        "the short name a supplier or client would recognise on an invoice - "
        "not a literal translation, not a sentence. "
        + ("Iraqi trade usage, not Modern Standard Arabic where the trade word "
           "differs. No article, no trailing punctuation, no explanation."
           if to_arabic else
           "Title Case. No article, no trailing punctuation, no explanation."),
        "",
        "Rules:",
        f"- name_{W} must not be blank. Use the most likely standard procurement "
        "term supported by the name and description.",
        "- Keep numbers, sizes and model codes exactly as written (3x2.5, 20mm, M20).",
        "- Use the unit vocabulary below verbatim. Do not invent unit spellings.",
        f"- description_{W} is optional: translate it only if the {other_lang} "
        "description says something a buyer needs. Otherwise return \"\".",
        "",
    ]
    if units:
        lines.append(f"Unit vocabulary ({other_lang} = the {wanted_lang} this "
                     "company uses):")
        lines += [f"  {asked} = {answer}" for asked, answer in sorted(units.items())]
        lines.append("")
    if glossary:
        lines.append("Names this company already uses, for style and vocabulary:")
        lines += [f"  {asked} = {answer}" for asked, answer in glossary]
        lines.append("")
    lines.append("Translate these items:")
    for i, item in enumerate(items):
        parts = [f'  {i}. name: {str(item.get("name") or "").strip()}']
        if str(item.get("description") or "").strip():
            parts.append(f'     description: {str(item["description"]).strip()}')
        if str(item.get("unit") or "").strip():
            parts.append(f'     unit: {str(item["unit"]).strip()}')
        candidates = item.get("candidates") if isinstance(item, dict) else []
        if isinstance(candidates, list):
            for candidate_index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    continue
                printable = [
                    str(candidate.get("name") or "").strip(),
                    str(candidate.get("description") or "").strip(),
                    str(candidate.get("unit") or "").strip(),
                ]
                printable = [value for value in printable if value]
                if printable:
                    parts.append(
                        f'     candidate {candidate_index}: ' + " | ".join(printable))
        lines += parts
    lines += [
        "",
        'Reply with JSON only: {"items": [{"index": 0, "name_%s": "...", '
        '"description_%s": "...", "unit_%s": "...", "candidate_index": 0}]}'
        % (W, W, W),
        "candidate_index is the listed zero-based candidate number when one "
        "candidate is the same material variant; otherwise return null.",
        "One entry per item, same indexes, no commentary.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #

def _post(url: str, payload: dict[str, Any],
          extra_headers: dict[str, str] | None = None,
          ) -> tuple[dict[str, Any] | None, str, bool]:
    """Returns (data, error, busy). `busy` means the service was overloaded and
    another model is worth trying; a 400/404 is OUR mistake and must be shown."""
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
            raw = resp.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            return None, "The translation service sent an unexpectedly large reply.", False
        return json.loads(raw.decode("utf-8", "replace")), "", False
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read(_MAX_BYTES).decode("utf-8", "replace")) \
                         .get("error", {}).get("message", "")
        except Exception:
            pass
        # Google's own words: a wrong model name or a bad key must say so.
        return None, f"Translation service refused the request ({exc.code}). {detail}".strip(), exc.code in _BUSY
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        # A TIMEOUT IS WORTH ANOTHER GO (2026-09-10). This model's latency is
        # wildly erratic - the identical request measured 0.9s, 3.6s, 14.3s and
        # over 30s within one minute - and a read timeout used to return
        # `busy=False`, which skipped the whole fallback chain and reported
        # "could not reach the translation service" for a service that was
        # merely slow. Pressing the button again almost always worked, which is
        # exactly what a retry is.
        #
        # A genuine connection failure (no network, DNS, refused) is NOT
        # retried: it will fail the same way four times and spend two minutes
        # doing it.
        reason = getattr(exc, "reason", exc)
        timed_out = isinstance(exc, (socket.timeout, TimeoutError))             or isinstance(reason, (socket.timeout, TimeoutError))
        if timed_out:
            return None, "The translation service did not answer in time.", True
        return None, f"Could not reach the translation service: {exc}", False
    except (ValueError, OSError) as exc:
        return None, f"Translation service gave an unreadable reply: {exc}", False


def _extract_text(data: dict[str, Any]) -> str:
    for cand in (data.get("candidates") or []):
        for part in ((cand.get("content") or {}).get("parts") or []):
            text = part.get("text")
            if text:
                return str(text)
    return ""


def _parse(text: str, items_or_count: int | list[dict[str, Any]],
           want: str = "en") -> list[dict[str, Any]]:
    """Read the model's JSON back, defensively.

    `responseMimeType` asks for bare JSON, but a fenced block or a stray
    sentence must not lose the whole batch.
    """
    to_arabic = str(want or "en").lower().startswith("ar")
    W = "ar" if to_arabic else "en"
    restore_missing = isinstance(items_or_count, list)
    request_items = items_or_count if restore_missing else [{}] * int(items_or_count)
    count = len(request_items)
    blob = text.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", blob).strip()
    try:
        data = json.loads(blob)
    except ValueError:
        match = re.search(r"\{.*\}", blob, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except ValueError:
            return []
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    out: dict[int, dict[str, Any]] = {}
    if restore_missing:
        out = {
            index: {
                "index": index,
                f"name_{W}": "",
                f"description_{W}": "",
                f"unit_{W}": "",
                "candidate_index": None,
            }
            for index in range(count)
        }
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        if type(index) is not int:
            continue
        if not 0 <= index < count:
            continue
        clean: dict[str, Any] = {"index": index}
        for key in (f"name_{W}", f"description_{W}", f"unit_{W}"):
            value = str(row.get(key) or "").strip()
            # A suggestion written in the language we ASKED ABOUT is the model
            # echoing the input back. That is not an answer, and it must not
            # look like one. Arabic in an English slot, or Latin script in an
            # Arabic one.
            echoed = (not _is_arabic(value)) if to_arabic else _is_arabic(value)
            clean[key] = "" if echoed else value
        clean["candidate_index"] = None
        candidate_index = row.get("candidate_index")
        candidates = request_items[index].get("candidates", []) \
            if isinstance(request_items[index], dict) else []
        if type(candidate_index) is int and isinstance(candidates, list) \
                and 0 <= candidate_index < len(candidates):
            clean["candidate_index"] = candidate_index
        out[index] = clean
    return [out[index] for index in sorted(out)]


def _one_request(chunk: list[dict[str, Any]], settings: dict[str, Any],
                 units: dict[str, str], want: str = "en",
                 ) -> tuple[list[dict[str, Any]], str, str, list[str]]:
    """One batch, one prompt, the model fallback. Returns
    (suggestions, error, model, tried)."""
    prompt = _build_prompt(chunk, units, _glossary(chunk, want=want), want)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    headers = {_KEY_HEADER: settings["api_key"]}

    # The chosen model first, then the fallbacks - but ONLY past a "busy".
    # A 400 or a 404 means the request was wrong, and asking a second model
    # the same wrong question just spends the user's quota and their time.
    tried: list[str] = []
    error = "The translation service did not answer."
    # The chosen model is worth a SECOND attempt before moving on: when it
    # times out the model is not the problem, the moment is.
    chain = [settings["model"], settings["model"]]
    chain += [m for m in _FALLBACK_MODELS if m != settings["model"]]
    deadline = time.monotonic() + _TOTAL_BUDGET
    for model in chain:
        if tried and time.monotonic() >= deadline:
            break
        tried.append(model)
        url = _ENDPOINT.format(model=urllib.parse.quote(model))
        data, error, busy = _post(url, payload, headers)
        if error:
            if busy:
                continue
            return [], error, model, tried
        suggestions = _parse(_extract_text(data or {}), chunk, want)
        if not suggestions:
            return [], "The translation service did not return any usable names.", model, tried
        return suggestions, "", model, tried
    return [], (f"{error} Tried {len(tried)} time{'' if len(tried) == 1 else 's'}. "
                "It is usually quick - press the button again."), "", tried


def suggest(items: list[dict[str, Any]], want: str = "en") -> dict[str, Any]:
    """The missing half of a batch of items, in `want`. Never raises.

    `want="en"` is a purchase order's Arabic items needing their English half;
    `want="ar"` is a quotation's English items needing their Arabic half, so
    the same goods are found when they are later ordered (2026-09-10).

    Returns `{"ok": True, "suggestions": [{index, name_en, description_en,
    unit_en}], "model": ...}`. Every value is a SUGGESTION for a human to accept
    - this function writes nothing and knows nothing about Master Data.

    A list longer than one request is SPLIT rather than refused. Refusing sent
    the user back to a forty-row popup with the button they just pressed doing
    nothing; A-88 alone had 46 rows, so the old cap was one ordinary purchase
    order away. If a later batch fails after earlier ones succeeded, the work
    already done is returned with a `warning` - throwing it away would make the
    user pay for it twice.
    """
    try:
        if not isinstance(items, list) or not items:
            return {"ok": False, "error": "Nothing to translate.", "suggestions": []}
        if len(items) > _MAX_TOTAL:
            return {"ok": False, "suggestions": [],
                    "error": f"Too many items at once (max {_MAX_TOTAL}). "
                             "Complete some rows, then press it again."}
        s = _settings()
        if not s["api_key"]:
            return {"ok": False, "suggestions": [], "error": status()["reason"]}

        try:
            from . import master_items
            units = master_items.unit_map(want)
        except Exception:
            units = {}

        out: list[dict[str, Any]] = []
        tried: list[str] = []
        model = ""
        for start in range(0, len(items), _MAX_ITEMS):
            chunk = items[start:start + _MAX_ITEMS]
            suggestions, error, used, chunk_tried = _one_request(chunk, s, units, want)
            for name in chunk_tried:
                if name not in tried:
                    tried.append(name)
            if error:
                if not out:
                    return {"ok": False, "suggestions": [], "error": error, "tried": tried}
                # Earlier batches landed. Keep them, and say what is missing.
                done = len(out)
                return {"ok": True, "suggestions": out, "model": model, "tried": tried,
                        "warning": f"{done} of {len(items)} items completed, then the "
                                   f"translation service stopped: {error}"}
            model = used or model
            # Indexes are the CALLER's, not this batch's.
            for row in suggestions:
                row["index"] = int(row["index"]) + start
            out.extend(suggestions)
        return {"ok": True, "suggestions": out, "model": model, "tried": tried}
    except Exception as exc:                     # nothing here may reach the caller
        return {"ok": False, "suggestions": [], "error": str(exc)}
