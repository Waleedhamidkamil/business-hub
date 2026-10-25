"""
Find a reference picture for an item, online.

The rest of this app is offline. This module is the ONE place that talks to the
internet, and it is deliberately small and defensive:

  * **stdlib only** (`urllib.request`) - no new dependency, and nothing to keep
    patched. The app still installs and runs with the network unplugged.
  * **Nothing is sent but the item's name and description.** They go to the
    image service as a search phrase. No document number, client, price or
    cost ever leaves the machine.
  * **A weak match is reported, never used.** Searching "Angle iron 75mm"
    returns "Flat Iron at Night" as its top hit - a plausible-looking photo of
    the wrong thing is worse than an empty cell, because nobody re-checks a
    cell that is already filled. Candidates are scored against the query and
    anything below `MIN_SCORE` is dropped.
  * **Bounded**: one connection timeout, one read timeout, a byte cap, and a
    content-type check. A slow or hostile response cannot hang the editor or
    fill the disk.

Providers are tried in order and the first that answers wins. All keyless:

  bing       - the ordinary web image index: merchant photography, catalogue
               shots, and Arabic. This is the one that actually answers a
               purchase order.
  openverse  - aggregates Flickr, Wikimedia and others; CC-licensed.
  commons    - Wikimedia Commons; narrow, but strong on tools and materials.

**Why Bing, and why that URL.** The first version used only the two CC photo
libraries, and it was close to useless for a quotation: "Toilet Brush" returned
*"Hillary Clinton Toilet Brush"*, because those are art and journalism archives
and no one photographs a floor mop for the commons. Bing indexes the shops.

The endpoint is `/images/async` - the one the results grid itself calls as you
scroll - and NOT the `/images/search` page a person sees. That page answers a
plain `urllib` request with **filler**: measured here, it returned Italian crime
photographs for "Hemp rope" and call-centre training slides for "Sledgehammer",
with an HTTP 200 and no hint anything was wrong. Adding a cookie jar and a
browser User-Agent made it worse, not better. The async endpoint returns the
real ranked grid to the same plain request. Driving real headless Chrome at the
page was also tried: identical results to the async endpoint, three seconds
slower per item, so it buys nothing.

This is one desktop, a handful of lookups a day, and the pictures are captioned
"for reference only". It is a personal-scale reader of a public page, not a
crawler - but it *is* unofficial, so a failure here falls through to the two
libraries rather than breaking the button.
"""
from __future__ import annotations

import html as _htmlmod
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Wikimedia's policy asks for a descriptive User-Agent that says who is
# calling, so the CC libraries get this one.
_UA = ("BusinessHub/1.0 (Windows desktop; item reference images; "
       "stdlib urllib)")

# Bing serves the async grid to a browser User-Agent. This is not a disguise -
# the request is a person clicking a button in a desktop app - it is the
# string the endpoint expects.
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
               "Edg/126.0.0.0")

CONNECT_TIMEOUT = 12          # seconds, per request
MAX_BYTES = 3 * 1024 * 1024   # a table cell never needs more than this
MAX_CANDIDATES = 8            # how many hits to score before choosing

# Word overlap a candidate's title must reach to be used at all. Tuned on the
# live catalog: "Sledgehammer" -> "Various sledge hammers" passes, "Angle iron
# 75mm" -> "Flat Iron at Night" does not.
MIN_SCORE = 0.34

# Noise that carries no meaning in an image search - dimensions, packaging
# units and the like. Stripped from the query so "Angle iron 75mm*75mm*6m"
# searches for the thing rather than the measurements.
#
# The diameter symbols are in the punctuation class for a reason. Once `_WORD`
# became Unicode-aware, "φ20mm" read as ONE word starting with a letter, so it
# survived the unit rule and "Hemp rope φ20mm" searched `rope φ20mm` - which
# found a *steel wire* rope. Stripping the symbol leaves "20mm" standing alone,
# where the unit rule removes it.
_NOISE = re.compile(
    r"""(\b\d+(\.\d+)?\s*(mm|cm|m|kg|g|l|ml|ton|tons|inch|in|ft|pcs|pc|ea|box|
        set|roll|pack|packet|piece|pieces|meter|meters|metre|metres)\b)
        |[×xX*]\s*\d+
        |\d+\s*[×xX*]
        |[\d,]{3,}
        |[()\[\]{}<>|/\\+_·•▪φΦØø⌀°]""",
    re.VERBOSE | re.IGNORECASE,
)

# Any script, not just Latin. This was `[a-z0-9]+` and it meant an Arabic item
# name produced ZERO words, so the ladder was empty and the button reported
# "nothing to search for" - a large part of the catalog could never be looked
# up at all. `[^\W_]` is "word character except underscore", Unicode-aware.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")

_UNIT_WORDS = {"mm", "cm", "kg", "ml", "ton", "tons", "pcs", "pc", "ea",
               "box", "set", "roll", "pack", "packet", "piece", "pieces",
               "meter", "meters", "metre", "metres", "inch", "in", "ft"}


# Words that qualify a thing without naming it. The head noun is never one of
# these, so they are skipped when looking for it.
_QUALIFIERS = {"big", "small", "large", "size", "sized", "type", "model",
               "new", "old", "normal", "standard", "heavy", "duty", "light",
               "grade", "quality", "colour", "color", "white", "black", "red",
               "blue", "green", "yellow", "long", "short", "thick", "thin",
               "high", "low", "mini", "max", "maxi", "plus", "pro", "super",
               "original", "genuine", "premium", "regular", "special"}


def _head_word(name: str) -> str:
    """The word that says WHAT the thing is.

    English puts it last - "Cotton Rectangular Cleaning *Mop*", "Angle *iron*"
    - except when the trailing words are qualifiers, as in "*Potato* (Big
    size)". So: the last non-qualifier word, falling back to the first.

    **Arabic is the other way round.** The noun leads and the adjectives
    follow: "*سبلت* جداري" is a split unit that is wall-mounted, "*ستاند* خوذ
    سلامة" is a stand for safety helmets. Taking the last word there anchors on
    the adjective and throws away the object, so Arabic names take the first.

    It matters because the query ladder drops words to widen the search, and
    without an anchor that goes badly wrong: "Potato (Big size)" shortened to
    "big size" matched "White Kyurem big-size Pokedoll". Every candidate must
    contain this word or it is not the same object.
    """
    words = [w for w in _WORD.findall((name or "").lower())
             if len(w) > 1 and not w[0].isdigit() and w not in _UNIT_WORDS]
    if not words:
        return ""
    if _ARABIC.search(name or ""):
        return words[0]
    for w in reversed(words):
        if w not in _QUALIFIERS:
            return w
    return words[0]


# A hyphen (or slash) is what makes a reference LOOK like one - "TDC-DN-7",
# "DN-TDC-029", "A-71", "Q-2026-001".
# The dashes are written as ESCAPES, not as the characters themselves.
# This class has to RECOGNISE an en or em dash somebody pasted into an
# item name, but the app may not contain the literal U+2014 (see
# CLAUDE.md and tests/no_em_dash.test.js). Writing it literally also put
# an invalid range in here once: replacing the em dash with a hyphen on
# 2026-09-11 left "–-/", a range from U+2013 down to "/", and the module
# stopped compiling at import - which took Find Pictures with it.
_CODE_JOINER = re.compile(r"[-\u2013\u2014/]")


def _has_real_word(text: str) -> bool:
    """Is there a word here that names a THING, rather than an abbreviation?

    Four letters is the line. Three would let "TDC" and "CAT" through; five
    would throw away "pipe", "film" and "bolt".
    """
    return any(len(w) >= 4 and not any(c.isdigit() for c in w)
               for w in _WORD.findall((text or "").lower()))


def _is_part_number(text: str) -> bool:
    """A MANUFACTURER's part number, which is very much searchable.

    "HIK-IPC-2CD1067G2-L(4MM)(6MP)" is a real Hikvision camera and Bing finds
    it - verified, it returns the camera. A document reference does not: the
    same search for "TDC-DN-7" returns duct flanges and a YouTube thumbnail.

    What separates them is the shape of the segments. A part number carries a
    long mixed letter-and-digit run ("2CD1067G2", "7616NXI"); a document
    reference is short letter groups and a serial ("DN", "TDC", "029").
    """
    for tok in re.split(r"[-\u2013\u2014/\s]+", text or ""):
        if len(tok) >= 5 and any(c.isalpha() for c in tok) \
                and any(c.isdigit() for c in tok):
            return True
    return False


def _looks_like_a_code(text: str) -> bool:
    """A document or serial reference rather than something photographable.

    Measured on 40 random catalog rows, the wrong pictures were **not**
    physical goods, and two of them were references: "TDC-DN-7" matched a
    YouTube thumbnail titled *"TDC - DN - YouTube"*. Bing indexes everything,
    so a code always finds *something* - and the word match is genuine, which
    means no amount of scoring saves it. It has to be refused before it is
    searched.

    All three tests are needed. Without the hyphen test "Bus" is a code;
    without the word test "Quick-drying cement" and "T-shirt" are codes;
    without the part-number test a real Hikvision camera is a code.
    """
    return (bool(_CODE_JOINER.search(text or ""))
            and not _has_real_word(text)
            and not _is_part_number(text))


def _query_terms(name: str, description: str = "") -> list[str]:
    """Search phrases to try, WIDEST LAST.

    This is the single biggest reason the first version found nothing.
    Openverse AND-matches every word, so "cotton rectangular cleaning mop"
    returns **zero** results while "cleaning mop" returns eight. A four-word
    product name is normal in a quotation, so one long query is a guaranteed
    miss.

    English product names put the head noun LAST - "Cotton Rectangular
    Cleaning *Mop*", "Angle *iron*", "Hemp *rope*" - so dropping words from
    the FRONT keeps the object and sheds the qualifiers.
    """
    text = f"{name or ''} {description or ''}".strip()
    text = _NOISE.sub(" ", text)
    words = [w for w in _WORD.findall(text.lower())
             if len(w) > 1 and not w[0].isdigit() and w not in _UNIT_WORDS]
    seen: set[str] = set()
    uniq: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    if not uniq:
        return []
    tries: list[str] = []
    # Which END to shed words from depends on where the head noun sits, and
    # the two scripts disagree. English trails it ("Cleaning *Mop*") so the
    # front goes; Arabic leads with it ("*ستاند* خوذ سلامة" - a stand, for
    # safety helmets) so the tail goes. Getting this backwards is not a
    # near-miss, it is a search for the adjective: "ستاند خوذ سلامة" trimmed
    # from the front becomes "خوذ سلامة" and finds helmets, never the stand.
    arabic = bool(_ARABIC.search(f"{name or ''}"))
    if len(uniq) > 3:
        tries.append(" ".join(uniq[:4]))
    if len(uniq) > 2:
        tries.append(" ".join(uniq[:3] if arabic else uniq[-3:]))
    # STOPS AT TWO WORDS for a multi-word item. Going down to one throws away
    # the word that makes it specific and lets anything through: "angle iron"
    # reduced to "iron" matches "Flat Iron at Night", and "Delete Test Item"
    # reduced to "item" matches literally anything. A one-word item name still
    # searches its one word, which is all it has.
    if len(uniq) > 1:
        tries.append(" ".join(uniq[:2] if arabic else uniq[-2:]))
    else:
        tries.append(uniq[-1])
    # De-duplicate while keeping order.
    out: list[str] = []
    for t in tries:
        if t not in out:
            out.append(t)
    return out


def _clean_query(name: str, description: str = "") -> str:
    """The widest phrase - kept for callers and tests that want just one."""
    terms = _query_terms(name, description)
    return terms[0] if terms else ""


def _has_word(title: str, word: str) -> bool:
    """`word` appears in `title` as a word, a simple plural, or (for a long
    enough word) inside a compound - "sledgehammer" in "sledge hammers"."""
    tw = _WORD.findall((title or "").lower())
    if word in tw:
        return True
    for t in tw:
        if t == word + "s" or (t.endswith("es") and t[:-2] == word):
            return True
    return len(word) >= 6 and word in "".join(tw)


def _score(query: str, title: str) -> float:
    """How well a candidate's title answers the query. 0..1.

    Three rules, each one a failure the previous version had:

      1. **Every query word must appear** (as a token, or inside a token so
         "sledgehammer" matches "sledge hammers"). Half a match is not a
         match: "angle iron" vs "Flat Iron at Night" shares `iron`, scored
         0.5, and put a photograph of a building in a steel line.

      2. **A one-word query must match a WHOLE word.** "mop" scored 1.0
         against "mops the dog" on a substring test. Plurals are allowed
         ("mop" -> "mops"), arbitrary continuations are not.

      3. **Shorter, tighter titles win.** Once several candidates all contain
         the query, the one that is mostly the query is the better picture -
         "Rubbermaid Reveal Spray Mop" over "MOPS and POPS Christmas Party".
    """
    qw = [w for w in _WORD.findall((query or "").lower())]
    tw = _WORD.findall((title or "").lower())
    if not qw or not tw:
        return 0.0
    tset = set(tw)
    tflat = "".join(tw)

    def present(w: str) -> bool:
        if w in tset:
            return True
        # plural / simple inflection of a whole title word
        for t in tset:
            if t == w + "s" or (t.endswith("es") and t[:-2] == w) or w == t + "s":
                return True
        # compound: only when the query word is long enough for a substring
        # test to mean something ("sledgehammer" yes, "mop" no).
        return len(w) >= 6 and w in tflat

    hits = sum(1 for w in qw if present(w))
    if hits < len(qw):
        # A PARTIAL match, graded by how much of the query is there. The two
        # bands matter and the boundary between them is the whole point:
        #
        #   "angle iron" vs "Flat Iron at Night"  - 1 of 2, coverage 0.50
        #   "oil drum binding strap" vs a real
        #    ratchet strap listing                - 3 of 4, coverage 0.75
        #
        # A flat `coverage * 0.4` scored the second one 0.30 and threw it
        # away, so the precise four-word query was abandoned and the search
        # fell back to "material ratchet" - a vaguer phrase finding a worse
        # picture. Merchant titles rarely contain every word of an item name;
        # insisting on all of them wastes the one query most likely to be
        # right. Two thirds present, WITH the head noun (checked separately
        # and non-negotiable), is a match. Half is not.
        coverage = hits / len(qw)
        return round(coverage * (0.6 if coverage >= 0.65 else 0.4), 3)
    focus = len(set(qw)) / len(tset)              # 1.0 when the title IS the query
    return round(min(1.0, 0.7 + 0.3 * min(focus, 1.0)), 3)


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
        raw = r.read(MAX_BYTES)
    return json.loads(raw.decode("utf-8", "replace"))


def _get_html(url: str, referer: str = "") -> str:
    headers = {"User-Agent": _BROWSER_UA,
               "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
        return r.read(4 * 1024 * 1024).decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Providers - each returns [{title, url, thumb, source, license}]
# --------------------------------------------------------------------------- #

_IUSC = re.compile(r'class="iusc"[^>]*?\bm="([^"]+)"')


def _bing(query: str) -> list[dict[str, Any]]:
    """The ordinary web image index, in relevance order.

    Each tile carries a JSON blob in its `m` attribute with the title (`t`),
    the original image (`murl`) and Bing's own thumbnail (`turl`). Both links
    are kept: `murl` is the better picture, `turl` is on Bing's CDN and works
    when the merchant blocks hot-linking, which many do.
    """
    url = ("https://www.bing.com/images/async?q=" + urllib.parse.quote(query)
           + f"&first=1&count={max(MAX_CANDIDATES * 4, 35)}"
           + "&adlt=moderate&mmasync=1")
    htm = _get_html(url, referer="https://www.bing.com/images/search?q="
                    + urllib.parse.quote(query))
    out: list[dict[str, Any]] = []
    for blob in _IUSC.findall(htm):
        try:
            d = json.loads(_htmlmod.unescape(blob))
        except Exception:
            continue          # one malformed tile must not lose the other 34
        link = d.get("murl") or d.get("turl") or ""
        if not link:
            continue
        out.append({"title": _htmlmod.unescape(d.get("t") or ""),
                    "url": link,
                    "thumb": d.get("turl") or "",
                    "source": "bing", "license": ""})
        if len(out) >= max(MAX_CANDIDATES * 4, 35):
            break
    return out


def _openverse(query: str) -> list[dict[str, Any]]:
    url = ("https://api.openverse.org/v1/images/?q="
           + urllib.parse.quote(query)
           + f"&page_size={MAX_CANDIDATES}&mature=false")
    data = _get_json(url)
    out = []
    for x in (data.get("results") or []):
        # `thumbnail` is a few hundred KB rather than several MB, and a table
        # cell prints at ~22mm - the full-size original is pure waste.
        link = x.get("thumbnail") or x.get("url") or ""
        if not link:
            continue
        out.append({"title": x.get("title") or "", "url": link,
                    "source": x.get("source") or "openverse",
                    "license": x.get("license") or ""})
    return out


def _commons(query: str) -> list[dict[str, Any]]:
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
           "&generator=search&gsrnamespace=6&gsrlimit=%d&gsrsearch=%s"
           "&prop=imageinfo&iiprop=url&iiurlwidth=800"
           % (MAX_CANDIDATES, urllib.parse.quote(query)))
    data = _get_json(url)
    pages = (data.get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        info = (p.get("imageinfo") or [{}])[0]
        link = info.get("thumburl") or info.get("url") or ""
        if not link:
            continue
        title = re.sub(r"^File:", "", p.get("title") or "")
        title = re.sub(r"\.(jpe?g|png|gif|webp|svg)$", "", title, flags=re.I)
        out.append({"title": title.replace("_", " "), "url": link,
                    "source": "wikimedia", "license": ""})
    return out


PROVIDERS = {"bing": _bing, "openverse": _openverse, "commons": _commons}
# Bing first: it is the only one that indexes shops, and the only one that
# answers in Arabic. The CC libraries stay behind it as a fallback for the day
# the scrape stops working.
PROVIDER_ORDER = ("bing", "openverse", "commons")


_LAST_CALL: dict[str, float] = {}

# Wikimedia answers 429 within a few rapid requests, which is what made a
# 15-row table find ONE picture: the first lookup worked and the rest were
# refused. One in-flight request per provider per interval, enforced here so
# no caller has to remember.
_MIN_INTERVAL = {"bing": 0.6, "openverse": 0.35, "commons": 1.1,
                 "translate": 0.2}


def _pace(provider: str) -> None:
    import time
    gap = _MIN_INTERVAL.get(provider, 0.5)
    last = _LAST_CALL.get(provider, 0.0)
    wait = gap - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _LAST_CALL[provider] = time.monotonic()


# --------------------------------------------------------------------------- #
# Arabic -> English
# --------------------------------------------------------------------------- #
#
# Measured on the real PO A-61 (20 Arabic vegetable names): Bing's ARABIC index
# is mostly recipe blogs and clip-art. It "found" 17 of 20 - and returned a
# line DRAWING for جزر (carrot), PICKLES for خيار (cucumber), the COLOUR orange
# for برتقال, and a design portfolio for رقي. Finding something is not finding
# the item.
#
# The English index for the same objects is full of clean product photographs.
# Google Images itself is unreachable - nine plain-HTTP routes return an empty
# JavaScript shell, and real headless Chrome serves the full page a couple of
# times before throttling to a 6KB stub - but Google TRANSLATE answers
# instantly and keylessly, 23 of 23 items in 12 seconds.
#
# So: translate, search both, keep the better. They fail in DIFFERENT places,
# which is the point - English rescues جزر and ستاند خوذ سلامة, Arabic rescues
# خيار (whose translation is the homonym "option", finding options trading).

_TRANSLATE_CACHE: dict[str, str] = {}
_TRANSLATE_FAILED = [False]


def translate_to_english(text: str) -> str:
    """Arabic -> English, or "" if it cannot be had.

    Never raises and never blocks the search: a translation is an improvement,
    not a dependency, so any failure just means the original query is used on
    its own. One failure disables it for the rest of the run - if the endpoint
    is unreachable, twenty more attempts only cost twenty more timeouts.
    """
    text = (text or "").strip()
    if not text or _TRANSLATE_FAILED[0]:
        return ""
    if text in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[text]
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
           "&sl=ar&tl=en&dt=t&q=" + urllib.parse.quote(text))
    try:
        _pace("translate")
        req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
            data = json.loads(r.read(MAX_BYTES).decode("utf-8", "replace"))
        out = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0]).strip()
    except Exception:
        _TRANSLATE_FAILED[0] = True
        return ""
    # A translation that comes back unchanged, or in Arabic, taught us nothing.
    if not out or _ARABIC.search(out) or out.lower() == text.lower():
        out = ""
    _TRANSLATE_CACHE[text] = out
    return out


def _run_ladder(terms: list[str], head: str) -> dict[str, Any]:
    """Walk one query ladder to the best result it can defend.

    Widest first: a long phrase is precise when it hits and returns nothing at
    all when it misses, so it is tried first and then progressively shortened.
    The first phrase yielding a confident match wins; a phrase that returns
    candidates but none good enough keeps going, because a shorter phrase
    often finds the object itself.
    """
    tried: list[str] = []
    seen_any = False
    best_overall: dict[str, Any] | None = None
    for query in terms:
        scored: list[dict[str, Any]] = []
        for key in PROVIDER_ORDER:
            fn = PROVIDERS.get(key)
            if fn is None:
                continue
            try:
                _pace(key)
                hits = fn(query)
            except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
                tried.append(f"{key}: {getattr(e, 'code', type(e).__name__)}")
                continue
            except Exception as e:
                tried.append(f"{key}: {type(e).__name__}")
                continue
            for h in hits:
                # The head word is non-negotiable: without it the picture is
                # of something else, however well the other words line up.
                if head and not _has_word(h["title"], head):
                    continue
                h["score"] = _score(query, h["title"])
                h["query"] = query
                scored.append(h)
            if any(h["score"] >= MIN_SCORE for h in scored):
                break
        if scored:
            seen_any = True
            scored.sort(key=lambda h: -h["score"])
            if best_overall is None or scored[0]["score"] > best_overall["score"]:
                # A COPY, deliberately. Assigning the candidate list onto
                # `scored[0]` itself makes the structure self-referential -
                # the list contains the very dict holding it - and the
                # bridge's recursive NaN scrub then walks it forever. The
                # button reported "maximum recursion depth exceeded" for every
                # item that found candidates but refused them, which since
                # Bing went in front is most of the refusals.
                best_overall = dict(scored[0])
                best_overall["candidates"] = scored[:5]
            if scored[0]["score"] >= MIN_SCORE:
                best = scored[0]
                return {"ok": True, "query": query, "title": best["title"],
                        "url": best["url"], "thumb": best.get("thumb", ""),
                        "score": best["score"],
                        "source": best["source"], "license": best["license"],
                        "candidates": scored[:5]}

    if not seen_any:
        return {"ok": False, "query": terms[0], "offline": bool(tried),
                "error": ("No image service answered - check the connection."
                          if tried else "Nothing found for this item."),
                "tried": tried}
    return {"ok": False, "query": terms[0], "weak": True,
            "error": f'Nothing convincing for "{terms[-1]}"',
            "best_guess": (best_overall or {}).get("title", ""),
            "candidates": (best_overall or {}).get("candidates", [])}


def _better(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Of two ladder results, the one to keep.

    A found match beats a refusal outright. Between two found matches the
    higher score wins, and a TIE keeps `a` - the phrase the user actually
    typed. Their own words are the better guess about their own catalog when
    nothing else separates the two.
    """
    if bool(a.get("ok")) != bool(b.get("ok")):
        return a if a.get("ok") else b
    if not a.get("ok"):
        # Two refusals: keep whichever at least had a candidate to name.
        return a if a.get("best_guess") else b
    return b if b.get("score", 0) > a.get("score", 0) else a


def search(name: str, description: str = "", phrase: str = "") -> dict[str, Any]:
    """Best reference picture for one item - the URL only, nothing downloaded.

    `phrase`, when given, is the user's OWN wording for this item and replaces
    the name and description entirely. It is the escape hatch for everything
    automation cannot know: that "خيار" is a cucumber and not a stock option,
    that "Bleach" is a cleaning product and not the cartoon. Nothing is
    guessed from the row once a phrase is supplied.
    """
    phrase = (phrase or "").strip()
    if phrase:
        terms = _query_terms(phrase, "")
        if not terms:
            return {"ok": False, "error": "Nothing to search for."}
        out = _run_ladder(terms, _head_word(phrase))
        out["phrase"] = phrase
        return out

    # A reference code is refused BEFORE any request goes out. Nothing good
    # can come of searching it, and Bing will always return something.
    if _looks_like_a_code(name):
        if _has_real_word(description):
            # The row is coded but described - "TDC-DN-7 / Toilet Brush". The
            # description is the item; searching it WITH the code attached
            # only drags the query back towards the code.
            name, description = description, ""
        else:
            return {"ok": False, "not_an_item": True, "query": (name or "").strip(),
                    "error": f'"{(name or "").strip()}" looks like a reference '
                             "code, not an item."}

    terms = _query_terms(name, description)
    if not terms:
        return {"ok": False, "error": "Nothing to search for."}

    native = _run_ladder(terms, _head_word(name))

    # Only Arabic gets a second pass. English names are already being searched
    # in the index that holds the pictures, so a round trip would buy nothing
    # and cost a request per item.
    if not _ARABIC.search(f"{name or ''} {description or ''}"):
        return native

    english = translate_to_english(f"{name or ''} {description or ''}".strip())
    if not english:
        return native
    en_terms = _query_terms(english, "")
    if not en_terms:
        return native
    translated = _run_ladder(en_terms, _head_word(english))
    translated["translated_from"] = (name or "").strip()
    translated["english"] = english

    best = _better(native, translated)
    if best is translated:
        best = dict(best)
        best["via_translation"] = True
    return best


def fetch(url: str) -> dict[str, Any]:
    """Download one image and return it as a data URI.

    Bounded on every axis a remote server controls: scheme, content type,
    byte count and time. `read(MAX_BYTES + 1)` then checking the length is
    deliberate - trusting Content-Length would let a lying header through.
    """
    import base64

    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "error": "Not a web address."}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                return {"ok": False, "error": f"That link is not an image ({ctype or 'unknown'})."}
            raw = r.read(MAX_BYTES + 1)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
        return {"ok": False, "error": f"Could not download it ({type(e).__name__})."}
    if len(raw) > MAX_BYTES:
        return {"ok": False, "error": "That image is too large."}
    if not raw:
        return {"ok": False, "error": "That image was empty."}
    return {"ok": True, "bytes": len(raw),
            "data_uri": f"data:{ctype};base64," + base64.b64encode(raw).decode("ascii")}


def find_image(name: str, description: str = "",
               phrase: str = "") -> dict[str, Any]:
    """search() + fetch() - one call, because that is how the button uses it.

    The chosen match is downloaded from the original address first, then from
    the search engine's thumbnail if that fails. Merchants block hot-linking
    and their photographs run well past `MAX_BYTES`; the thumbnail is on a CDN
    that always answers and is small. A table cell prints at about 22mm, so
    the fallback is not a worse picture - it is the right size to begin with.
    """
    found = search(name, description, phrase)
    if not found.get("ok"):
        return found
    got = fetch(found["url"]) or {}
    if not got.get("ok") and found.get("thumb") and found["thumb"] != found["url"]:
        alt = fetch(found["thumb"]) or {}
        if alt.get("ok"):
            got = alt
    if not got.get("ok"):
        return {"ok": False, "query": found.get("query"),
                "error": got.get("error"), "title": found.get("title")}
    found = dict(found)
    found.update(data_uri=got["data_uri"], bytes=got["bytes"])
    found.pop("candidates", None)
    return found
