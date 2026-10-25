"""
Central configuration.

Defaults are defined below. At import time we also load `data/settings.json`
(if present) and override any matching key - so the in-app Settings panel
can change paths, signatures, layout tuning, etc. without editing this file.

The hot-reload in pdf_generator picks up changes here automatically, so a
Settings-save call triggers `importlib.reload(config)` and the next PDF
render uses the new values.
"""
import json
from pathlib import Path

from . import doc_settings as _doc_settings
from . import profile_manager

# Where finished POs are filed. Default is now under DOCUMENTS_ROOT so all
# four doc types live in a single managed folder tree. Override via Settings.
PO_ROOT = None     # set below, after DOCUMENTS_ROOT is defined

# Where the app itself lives (db, templates, static, logo).
APP_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
ACTIVE_PROFILE = profile_manager.active_profile_id()
PROFILE_DIR = profile_manager.profile_dir()
DATA_DIR = PROFILE_DIR
DB_PATH = profile_manager.profile_db_path()
SETTINGS_PATH = profile_manager.settings_path()
ASSETS_DIR = profile_manager.assets_dir()
PROFILE_ACCENT = profile_manager.ProfileManager.PROFILE_ACCENTS[ACTIVE_PROFILE]
# USD peg used for profit/costing math (IQD costs → USD). Per profile:
# Abraj 1$=1400 IQD, Eshraqat 1$=1500 IQD.
IQD_PER_USD = 1500.0 if ACTIVE_PROFILE == "eshraqat" else 1400.0
# Cash deals settle at the market rate in EVERY profile - a deal whose invoice
# is paid in cash uses this instead of the profile peg (see database.doc_iqd_per_usd).
CASH_IQD_PER_USD = 1500.0
# A billable line with NO cost entered is not pure profit (2026-10-21, Waleed).
# Every profit figure assumes it was sold at this markup on cost, so its cost
# is estimated as price / (1 + markup): $9,000 -> ~$6,430 cost, ~$2,570 profit.
# Only the MATHS uses it - the line still counts as uncosted everywhere (the
# Uncosted flag, the To-Do), so the real cost is still asked for. See
# database._line_econ.
UNCOSTED_MARKUP = 0.40

ESHRAQAT_IDENTITY = {
    "name_lines": [
        "Eshraqat Albadr",
        "for Contracting, General Services,",
        "Heavy Equipment Rental, and Employment of Iraqi Labor",
    ],
    # 2026-08-09 - the Arabic identity. Only the PO skin is Arabic/RTL today
    # (`templates/eshraqat/po_page.html`); the other four eshraqat documents
    # stay English and read `name_lines` above. Keep both lists in sync when
    # the registered company name changes.
    "name_lines_ar": [
        "شركة اشراقات البدر",
        "للتجارة والمقاولات العامة",
        "وتأجير الاليات الثقيلة وتشغيل الايدي العاملة",
        "(مشروع ماء البحر المشترك)",
    ],
    "po_title_ar": "طلب شراء",
    "website": "www.eshraqatalbadr.com",
    "emails": ["info@eshraqatalbadr.com", "eshraqat.albadr@gmail.com"],
    "brand_color": "#861432",
}

# Map month number -> folder name used under PO_ROOT.
# Adjust if your folder names differ (e.g. full names, Arabic names, etc.).
MONTH_FOLDERS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# PO number format
PO_PREFIX = "A"

# --- Page-splitting ---------------------------------------------------------
# Rule #6 (refined): the table is split into a new PO whenever it would
# either (a) exceed the vertical content budget below, or (b) hit the
# absolute item ceiling. Both must hold; whichever triggers first wins.
#
# The vertical budget is calculated from the printable A4 area minus the
# header, title, meta row, and the absolutely-positioned signature box.
# See pdf_generator.estimate_row_height_mm for the per-row math.
# 2026-07-29 - a PO now PAGINATES like every other document type (one PO number,
# N pages) instead of splitting into several POs. The per-page vertical budget is
# therefore derived per page ROLE from measured geometry in
# `pdf_generator` (PO_USABLE_MM / PO_HEADER_MM / PO_SIGNATURE_MM), not from a
# single flat number here. `MAX_ITEMS_PER_PO` and `TABLE_BUDGET_MM` are retired:
# the cap existed only to force the split, and one flat budget cannot describe a
# first page (header, no signature) and a middle page (neither) at once.
PO_AUTOFIT = False               # when True, each PO row hugs its text (no fixed row height)
# Row model - measured against the real renderer (see the calibration note in
# pdf_generator.estimate_row_height_mm). A single-line row renders at 6.61mm and
# each extra wrapped line adds 5.30mm; these round UP so the estimate stays on
# the safe side of the signature without breaking pages a third of the way down.
TABLE_HEADER_MM = 12             # height the <thead> row takes (renders 10.32; the
                                 # 1.7mm over-book is deliberate slack)
# 2026-09-05 - this is now the RENDERED height of a single-line PO row, not a
# guess at one: po_styles.css sets `height: var(--po-row-mm)` with border-box,
# so whatever stands here is what the browser draws. The old 7 was measured
# against the CONTENT box and missed the 6px padding + border the same rule
# added, which is how A-84 planned 27 rows onto a sheet that holds 21.
# Measured in Chrome at 10.32mm; 10.3 keeps a hair of slack.
TABLE_MIN_ROW_MM = 10.3          # height of a single-line data row (renders 10.32)
TABLE_LINE_HEIGHT_MM = 5.5       # added height per extra text line (measured 5.30)
# Vertical band the manager signature block occupies at the bottom of the LAST
# page, reserved so the table can never run into it. Measured with sig 1 + stamp
# applied: the stamp image's top edge lands 51.3mm above the page box bottom;
# 54 leaves room for the per-render random placement jitter. Tunable because a
# taller stamp/signature asset changes it - the rest of the PO page geometry is
# a property of the template and lives in `pdf_generator`.
PO_SIGNATURE_MM = 54.0

# Brand color (taken from the xlsx: FF002060 = dark navy)
BRAND_COLOR = "#002060"

# Company / signature info shown on the PO - matches ABRAJ PO.xlsx exactly.
COMPANY = {
    "name_ar_lines": [
        "شركة ابراج البدر",
        "للتجارة العامة والمقاولات",
        "وتأجير الاليات الثقيلة",
        "(مشروع تجهيز ماء البحر المشترك)",
    ],
    "name_en_lines": [
        "ABRAJ ALBADER CO.",
        "FOR GENERAL TRADING, CONTRACTING",
        "AND HEAVY MACHINERY RENTAL",
        "(COMMON SEAWATER SUPPLY PROJECT)",
    ],
    "title_ar": "طلب تجهيز مواد",
    "submitter_label_ar": "مقدم الطلب",
    "submitter_name_ar": "وليد حامد كامل",
    "manager_label_ar": "المدير العام",
    "manager_name_ar": "علي مجيد جلوب",
}


# ============================================================
# QUOTATION (English, LTR) - materials quote (v1)
# ============================================================
# Single base folder under which every document type lives.
# Quotations save to: <DOCUMENTS_ROOT>/QUOTATION/<client>/<year>/<TYPE>/<month>/
# Future doc types (INV, DN) will follow the same convention.
# Configurable from the in-app Settings (key: "documents_root").
DOCUMENTS_ROOT = Path(r"D:\PO TOOL")

# App-wide UI language for the chrome (buttons, modals, settings). The PO
# section keeps Arabic regardless because it's an Arabic document. "en" | "ar".
UI_LANG = "en"

# App-wide user preferences. These are UI conveniences (not document content)
# and live in data/settings.json under "preferences" so WebView storage loss
# does not reset them between launches.
USER_PREFERENCES = {
    "theme": "light",
    "after_export": "copy",
    "compact_tables": False,
    "dn_prices_default": False,
}

# PO per-line company classification. Maps a company name (as typed in the PO
# "لشركة" cell) to a tag: "client" (billable - needs a delivery note + invoice)
# or "internal" (our own office, e.g. ابراج البدر - never billed). Stored in
# settings.json under "po_company_tags" (per profile). An UNTAGGED non-blank
# name is treated as "client" by the To-Do so nothing billable is silently
# dropped; only an explicit "internal" tag silences a name. See
# po_company_is_internal() and database.pos_awaiting_invoice().
PO_COMPANY_TAGS: dict = {}

# Default PO root now lives under DOCUMENTS_ROOT.
PO_ROOT = DOCUMENTS_ROOT / "PURCHASE ORDERS"

QUOTE_ROOT = DOCUMENTS_ROOT / "QUOTATIONS"   # derived; do not edit directly
QUOTE_PREFIX = "QUT"
QUOTE_TYPE_CODES = {
    "materials": "MT",
    "manpower":  "MP",
    "equipment": "EQ",
    "service":   "SV",
}
QUOTE_ADDRESS_OPTIONS = ["CSSP Site", "IBRU"]
QUOTE_CLIENTS_SEED = [
    "HDEC", "Gusan", "Daesun", "77 Insaat", "Daeah",
    "Chemie-Tech", "Daeshin", "ILK Construction", "JGC Corporation",
]
QUOTE_TERMS_DEFAULT = [
    "Currency  : USD",
    "Withholding tax  : 3.3%",
    "Delivery within 5 days",
]
QUOTE_BRAND_COLOR = "#1F3864"               # navy used on the quote header
QUOTE_COMPANY = {
    "name_lines": [
        "ABRAJ ALBADR CO.",
        "General Trading, Contracting,",
        "And Heavy Machinery Rental",
    ],
    "title": "Quotation",
    "closing": ("Hope you will find our rates most competitive and we look "
                "forward to have a cordial and long business relation with "
                "your company."),
    "signature_name": "Waleed Hamid",
    "signature_title": "Procurement Administrator",
}
# Quote table fitting (mm). Few items -> tall rows; many -> compact.
QUOTE_TABLE_AREA_MM = 120     # target vertical fill for the tbody
QUOTE_MIN_ROW_MM    = 12      # most compact a data row gets
QUOTE_MAX_ROW_MM    = 55      # tallest a single/few-item row gets

# Manpower-specific terms (per client request).
QUOTE_TERMS_EQUIPMENT = [
    "Currency  : USD",
    "Withholding tax  : 3.3%",
    "The attached images and vehicle colors are for reference only",
]

QUOTE_TERMS_MANPOWER = [
    "All terms and calculations shall be in accordance with Iraqi Labour Law.",
    "Transportation between Basra and the project site (both directions) shall be the Supplier's responsibility.",
    "Meals and accommodation shall be the Client's responsibility.",
    "The Client shall provide a minimum of seven (7) days' notice prior to the termination of any employee or worker.",
    "If the Client requests demobilization of any worker who commenced work within the first week of the month, the full monthly salary shall be payable.",
    "Annual leave entitlement shall be twenty-one (21) days.",
    "The Supplier shall provide the necessary PPE for personnel, if required.",
]

# ---- Quotation type schemas -------------------------------------------------
# Each type defines its data columns (the "NO." index column is added
# automatically) and its default terms. Column kinds:
#   text     -> free text (wraps; counts toward row height)
#   num      -> plain number (qty)
#   money    -> rendered as $X.XX in the PDF
#   computed -> qty * rate (materials TOTAL column)
#   image    -> blank cell (image support is v2)
# Data-column widths sum to ~96.4 (the NO. column takes the remaining 3.6).
QUOTE_TYPES = {
    "materials": {
        "code": "MT", "label": "Materials",
        "grand_total": True,
        "terms": QUOTE_TERMS_DEFAULT,
        "primary_key": "item",          # column the autocomplete is anchored on
        # Phase L.5 - rebalanced after preview check:
        #   • RATE/TOTAL bumped so "$1,170.00" doesn't wrap to two lines
        #   • DESCRIPTION trimmed (was bloated at 28% with no proportional
        #     gain - content rarely needs that much; long entries can still
        #     wrap normally inside this width)
        #   • IMAGE trimmed (visual size of ~22% is plenty; oversized image
        #     columns were pushing rows taller in the rendered PDF)
        "columns": [
            {"key": "item",        "head": "ITEM",            "kind": "text",     "width": 16.0},
            {"key": "description", "head": "DESCRIPTION",     "kind": "text",     "width": 22.0},
            {"key": "unit",        "head": "UNIT",            "kind": "text",     "width":  6.0},
            {"key": "qty",         "head": "QTY",             "kind": "num",      "width":  6.0},
            {"key": "rate",        "head": "RATE",            "kind": "money",    "width": 12.0},
            {"key": "total",       "head": "TOTAL",           "kind": "computed", "width": 12.0},
            {"key": "image",       "head": "IMAGE",           "kind": "image",    "width": 22.4},
        ],
    },
    "equipment": {
        "code": "EQ", "label": "Equipment",
        "grand_total": False,
        "terms": QUOTE_TERMS_EQUIPMENT,
        "primary_key": "equipment",
        # Phase L.4.1 - more room for Specs + Image; tighter Rate. Header
        # "IMAGE" instead of "IMAGE (IF ANY)".
        "columns": [
            {"key": "equipment", "head": "Vehicle/Equipment Type", "kind": "text",  "width": 20.0},
            {"key": "specs",     "head": "Specifications",         "kind": "text",  "width": 38.0},
            {"key": "rate",      "head": "Monthly Rental Rate",    "kind": "money", "width": 14.0},
            {"key": "image",     "head": "IMAGE",                  "kind": "image", "width": 24.4},
        ],
    },
    "manpower": {
        "code": "MP", "label": "Manpower",
        "grand_total": False,
        "terms": QUOTE_TERMS_MANPOWER,
        "primary_key": "role",
        "columns": [
            {"key": "role", "head": "Role Description",  "kind": "text",  "width": 50.0},
            {"key": "unit", "head": "Unit of Measure",   "kind": "text",  "width": 23.0, "default": "Per Hour"},
            {"key": "rate", "head": "Rate",              "kind": "money", "width": 23.4},
        ],
    },
    "service": {
        "code": "SV", "label": "Service",
        "grand_total": False,
        "terms": QUOTE_TERMS_DEFAULT,
        "primary_key": "description",
        "columns": [
            {"key": "description", "head": "Description", "kind": "text",  "width": 36.0},
            {"key": "unit",        "head": "Unit",        "kind": "text",  "width": 12.0},
            {"key": "rate",        "head": "Rate",        "kind": "money", "width": 16.0},
            {"key": "remarks",     "head": "Remarks",     "kind": "text",  "width": 32.4},
        ],
    },
}


# ============================================================
# DELIVERY NOTE (English, LTR) - matches DN REF.pdf
# ============================================================
DN_ROOT = DOCUMENTS_ROOT / "DELIVERY NOTES"     # <BASE>\DELIVERY NOTES\<Client>\<Year>\<Month>\
DN_PREFIX = "DN"
DN_BRAND_COLOR = "#1F3864"                # same navy as quotation
DN_COMPANY = {
    "name_lines": [
        "ABRAJ ALBADR CO.",
        "General Trading, Contracting,",
        "And Heavy Machinery Rental",
    ],
    "title": "Delivery Note",
    "closing": "Thank You For Considering Abraj Al-Bader Co.",
    "recipient_label": "Recipient Signature",
    "supplier_label":  "Supplier Signature",
}
# Address dropdown defaults (reuse the quote ones - same options).
DN_ADDRESS_OPTIONS = list(QUOTE_ADDRESS_OPTIONS)
# DN table columns. When `include_prices` is True the editor & PDF add
# Rate + Total columns to the right of Q'ty. When False, only the base columns.
DN_BASE_COLUMNS = [
    {"key": "item",        "head": "Item",        "kind": "text",  "width": 25.0},
    {"key": "description", "head": "Description", "kind": "text",  "width": 38.0},
    {"key": "unit",        "head": "Unit/Size",   "kind": "text",  "width": 14.0},
    {"key": "qty",         "head": "Q'ty",        "kind": "num",   "width": 9.4},
]
DN_PRICE_COLUMNS = [
    {"key": "rate",  "head": "Rate",  "kind": "money",    "width": 11.0},
    {"key": "total", "head": "Total", "kind": "computed", "width": 12.0},
]


# ============================================================
# INVOICE (English, LTR) - matches INV REF.pdf
# ============================================================
# Path: <BASE>\INVOICES\<Client>\<Year>\<Month>\  (type-code stays in the filename, not the folder)
INV_ROOT = DOCUMENTS_ROOT / "INVOICES"
INV_PREFIX = "INV"
INV_BRAND_COLOR = "#1F3864"
INV_WITHHOLDING_PCT = 3.3
INV_COMPANY = {
    "name_lines": [
        "ABRAJ ALBADR CO.",
        "General Trading, Contracting,",
        "And Heavy Machinery Rental",
    ],
    "closing": "Thank You For Considering ABRAJ ALBADR CO.",
    "signature_label": "General Manager",
    "signature_name": "Eng. Ali Majeed Ch.",
    "tax_label": "Withholding Tax (3.3 %)",
    "tax_status_label": "Included",
}
INV_TYPE_CODES = {
    "materials": "MT",
    "manpower":  "MP",
    "equipment": "EQ",
    "service":   "SV",
}
INV_ADDRESS_OPTIONS = list(QUOTE_ADDRESS_OPTIONS)

# Bank info options (selectable in the editor). Each entry renders the same
# block layout in the Payment Info card. Currency is per-bank so you can
# carry USD + IQD accounts later without code changes.
#
# Phase G.12: the special "cash" key is sentinel-only - when picked, the
# Payment Info card is omitted from the PDF entirely.
CASH_BANK = {
    "key": "cash",
    "label": "Cash (no bank details)",
    "company_line": "",
    "bank_name": "",
    "branch":    "",
    "swift":     "",
    "account_no":"",
    "iban":      "",
    "currency":  "USD",
}

def _bank_rate(value):
    """A usable IQD-per-USD rate, or None. Zero and negatives are not rates,
    and a blank box means "not set", not "free"."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


# The rate a document is costed at when its client has no receiving account.
# Overridden per profile by `invoice.fallback_iqd_per_usd`.
INV_FALLBACK_IQD_PER_USD = 1500.0

INV_BANK_INFO = [
    {
        "key": "adib",
        "label": "Abu Dhabi Islamic Bank (USD)",
        "company_line": "ABRAJ AL BADER FOR CONTRACTING GENERAL T",
        "bank_name": "Abu Dhabi Islamic Bank",
        "branch":    "Abu Dhabi",
        "swift":     "ABDIAEADXXX",
        "account_no":"19616543",
        "iban":      "AE880500000000019616543",
        "currency":  "USD",
    },
    {
        "key": "bob",
        "label": "Bank of Baghdad (USD)",
        "company_line": "ABRAJ AL BADER FOR CONTRACTING GENERAL T",
        "bank_name": "Bank of Baghdad",
        "branch":    "Baghdad",
        "swift":     "BBADIQBAXXX",
        "account_no":"",
        "iban":      "",
        "currency":  "USD",
    },
    dict(CASH_BANK),
]

# Per-type schemas. Same kind vocabulary as QUOTE_TYPES.
# Data-column widths sum to ~95.4 (NO. = 4.6).
# Per-doc-type table layout overrides (Phase F.5). Empty means use the
# defaults baked into each generator module.
QUOTE_LAYOUT: dict = {}
DN_LAYOUT:    dict = {}
INV_LAYOUT:   dict = {}

# Per-sub-type overrides (Phase G.2). Schema:
#   {"materials": {"terms": [...], "column_heads": {"item": "Material"}, "layout": {...}}, ...}
QUOTE_SUBTYPES_OVERRIDES: dict = {}
INV_SUBTYPES_OVERRIDES:   dict = {}

# Phase G.7 - DN doesn't have sub-types, so the columns override lives at
# the top of the delivery section as a flat list.
DN_BASE_COLUMNS_OVERRIDE: list = []

INV_TYPES = {
    "materials": {
        "code": "MT", "label": "Materials Invoice",
        "primary_key": "item",
        "columns": [
            {"key": "item",        "head": "Item",        "kind": "text",     "width": 18.0},
            {"key": "description", "head": "Description", "kind": "text",     "width": 28.0},
            {"key": "unit",        "head": "Unit/Size",   "kind": "text",     "width": 11.0},
            {"key": "qty",         "head": "Q'ty",        "kind": "num",      "width": 8.0},
            {"key": "rate",        "head": "Rate",        "kind": "money",    "width": 14.0},
            {"key": "total",       "head": "Total",       "kind": "computed", "width": 16.4},
        ],
    },
    "equipment": {
        "code": "EQ", "label": "Equipment Invoice",
        "primary_key": "equipment",
        "columns": [
            {"key": "equipment",   "head": "Vehicle/Equipment", "kind": "text",     "width": 22.0},
            {"key": "description", "head": "Description",       "kind": "text",     "width": 25.0},
            {"key": "unit",        "head": "Period",            "kind": "text",     "width": 12.0, "default": "Per Month"},
            {"key": "qty",         "head": "Q'ty",              "kind": "num",      "width": 8.0},
            {"key": "rate",        "head": "Rate",              "kind": "money",    "width": 14.0},
            {"key": "total",       "head": "Total",             "kind": "computed", "width": 14.4},
        ],
    },
    "manpower": {
        "code": "MP", "label": "Manpower Invoice",
        "primary_key": "role",
        "columns": [
            {"key": "role",        "head": "Role",        "kind": "text",     "width": 22.0},
            {"key": "description", "head": "Description", "kind": "text",     "width": 25.0},
            {"key": "unit",        "head": "Period",      "kind": "text",     "width": 12.0, "default": "Per Month"},
            {"key": "qty",         "head": "Q'ty",        "kind": "num",      "width": 8.0},
            {"key": "rate",        "head": "Rate",        "kind": "money",    "width": 14.0},
            {"key": "total",       "head": "Total",       "kind": "computed", "width": 14.4},
        ],
    },
    "service": {
        "code": "SV", "label": "Service Invoice",
        "primary_key": "description",
        "columns": [
            {"key": "description", "head": "Description", "kind": "text",     "width": 45.0},
            {"key": "unit",        "head": "Unit",        "kind": "text",     "width": 10.0},
            {"key": "qty",         "head": "Q'ty",        "kind": "num",      "width": 8.0},
            {"key": "rate",        "head": "Rate",        "kind": "money",    "width": 16.0},
            {"key": "total",       "head": "Total",       "kind": "computed", "width": 16.4},
        ],
    },
}


def _apply_company_profile_defaults() -> None:
    """Overlay built-in identity defaults before profile-local settings."""

    if ACTIVE_PROFILE != "eshraqat":
        return

    global DOCUMENTS_ROOT, PO_ROOT, QUOTE_ROOT, DN_ROOT, INV_ROOT
    global PO_PREFIX, QUOTE_PREFIX, DN_PREFIX, INV_PREFIX
    global BRAND_COLOR, QUOTE_BRAND_COLOR, DN_BRAND_COLOR, INV_BRAND_COLOR
    global COMPANY, QUOTE_COMPANY, DN_COMPANY, INV_COMPANY
    global INV_BANK_INFO
    global QUOTE_ADDRESS_OPTIONS, DN_ADDRESS_OPTIONS, INV_ADDRESS_OPTIONS

    DOCUMENTS_ROOT = Path(r"D:\ESHRAQAT DOCUMENTS")
    PO_ROOT = DOCUMENTS_ROOT / "PURCHASE ORDERS"
    QUOTE_ROOT = DOCUMENTS_ROOT / "QUOTATIONS"
    DN_ROOT = DOCUMENTS_ROOT / "DELIVERY NOTES"
    INV_ROOT = DOCUMENTS_ROOT / "INVOICES"

    PO_PREFIX = "PO"
    QUOTE_PREFIX = "QT"
    DN_PREFIX = "DN"
    INV_PREFIX = "IV"

    BRAND_COLOR = PROFILE_ACCENT
    QUOTE_BRAND_COLOR = PROFILE_ACCENT
    DN_BRAND_COLOR = PROFILE_ACCENT
    INV_BRAND_COLOR = PROFILE_ACCENT

    identity_lines = [
        "ESHRAQAT ALBADR",
        "Contracting, General Services, Heavy Equipment Rental,",
        "and Employment of Iraqi Labor",
    ]
    COMPANY = {
        **COMPANY,
        "name_ar_lines": [],
        "name_en_lines": list(identity_lines),
        "submitter_name_ar": "",
        "manager_name_ar": "",
    }
    QUOTE_COMPANY = {
        **QUOTE_COMPANY,
        "name_lines": list(identity_lines),
        "title": "Quotation",
        "signature_name": "",
        "signature_title": "",
    }
    DN_COMPANY = {
        **DN_COMPANY,
        "name_lines": list(identity_lines),
        "closing": "Thank you for considering Eshraqat Albadr.",
    }
    INV_COMPANY = {
        **INV_COMPANY,
        "name_lines": list(identity_lines),
        "closing": "Thank you for considering Eshraqat Albadr.",
        "signature_label": "",
        "signature_name": "",
    }
    INV_BANK_INFO = [dict(CASH_BANK)]
    QUOTE_ADDRESS_OPTIONS = []
    DN_ADDRESS_OPTIONS = []
    INV_ADDRESS_OPTIONS = []


_apply_company_profile_defaults()


# --- User settings overlay --------------------------------------------------
# Anything in data/settings.json wins over the defaults above.
# Schema (all keys optional):
#   po_root: str path
#   po_prefix: str
#   max_items_per_po: int
#   table_budget_mm: int
#   brand_color: "#RRGGBB"
#   company: { ...keys matching COMPANY dict... }
def load_user_settings() -> dict:
    """Return the parsed settings JSON (empty dict if missing or unreadable)."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _apply_user_settings() -> None:
    """Mutate this module's globals based on data/settings.json.

    Schema:
      - Flat top-level keys cover the PO module (legacy).
      - Per-doc-type sections under "quotation", "delivery", "invoice"
        override that doc-type's COMPANY block and brand color.
      - "invoice.bank_info" replaces the bank list entirely (each entry is
        a dict with key/label/company_line/bank_name/branch/swift/account_no/
        iban/currency).
    """
    global PO_ROOT, PO_PREFIX, PO_AUTOFIT
    global TABLE_HEADER_MM, TABLE_MIN_ROW_MM, TABLE_LINE_HEIGHT_MM, PO_SIGNATURE_MM
    global BRAND_COLOR, COMPANY
    global DOCUMENTS_ROOT, QUOTE_ROOT, DN_ROOT, INV_ROOT
    global QUOTE_BRAND_COLOR, QUOTE_COMPANY, QUOTE_ADDRESS_OPTIONS
    global DN_BRAND_COLOR, DN_COMPANY, DN_ADDRESS_OPTIONS
    global INV_BRAND_COLOR, INV_COMPANY, INV_ADDRESS_OPTIONS
    global INV_BANK_INFO, INV_WITHHOLDING_PCT, INV_FALLBACK_IQD_PER_USD
    global USER_PREFERENCES, PO_COMPANY_TAGS

    s = load_user_settings()
    if not s:
        return

    # ---- top-level / shared paths ----
    if "documents_root" in s and s["documents_root"]:
        DOCUMENTS_ROOT = Path(s["documents_root"])
        QUOTE_ROOT = DOCUMENTS_ROOT / "QUOTATIONS"
        DN_ROOT    = DOCUMENTS_ROOT / "DELIVERY NOTES"
        INV_ROOT   = DOCUMENTS_ROOT / "INVOICES"
        # PO follows the documents root unless explicitly pinned elsewhere.
        if not (s.get("po_root") or "").strip():
            PO_ROOT = DOCUMENTS_ROOT / "PURCHASE ORDERS"
    if "po_root" in s and s["po_root"]:
        PO_ROOT = Path(s["po_root"])
    # ---- Purchase Order ----
    # 2026-09-05 - read through the ONE declaration in `doc_settings`, which
    # looks in the `purchase_order` section first and falls back to the old
    # top-level spelling. That fallback is what keeps an un-migrated profile, or
    # a settings.json restored from an older backup, working. `max_items_per_po`
    # / `table_budget_mm` are deliberately NOT read (2026-07-29 - POs paginate
    # instead of splitting); old files still carry them and ignoring them is
    # intentional, not an omission.
    _po = {f.target: _doc_settings.read(s, "po", f) for f in _doc_settings.PO_FIELDS}

    def _po_val(target):
        v = _po.get(target)
        return None if _doc_settings.is_missing(v) else v

    _prefix = _po_val("PO_PREFIX")
    if _prefix:
        PO_PREFIX = str(_prefix).strip()
    _autofit = _po_val("PO_AUTOFIT")
    PO_AUTOFIT = bool(_autofit) if _autofit is not None else False
    for _target in ("TABLE_HEADER_MM", "PO_SIGNATURE_MM",
                    "TABLE_MIN_ROW_MM", "TABLE_LINE_HEIGHT_MM"):
        _raw = _po_val(_target)
        if _raw is None:
            continue
        try:
            globals()[_target] = float(_raw)
        except (TypeError, ValueError):
            pass
    _brand = _po_val("BRAND_COLOR")
    if _brand:
        BRAND_COLOR = str(_brand).strip()
    _company = _po_val("COMPANY")
    if isinstance(_company, dict):
        merged = {**COMPANY}
        for k, v in _company.items():
            if v is not None:
                merged[k] = v
        COMPANY = merged
    # App-wide UI language (chrome only - PDF content is unaffected).
    global UI_LANG
    if "ui_lang" in s and str(s["ui_lang"] or "").strip():
        UI_LANG = str(s["ui_lang"]).strip().lower()

    # ---- app user preferences (UI-only, persisted in settings.json) ----
    def _pref_bool(v, default=False) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "on")

    raw_prefs = s.get("preferences") if isinstance(s.get("preferences"), dict) else {}
    # Backward-compatible escape hatch if a future/older build writes these
    # directly at top level.
    for legacy_key in ("theme", "after_export", "compact_tables", "dn_prices_default"):
        if legacy_key in s and legacy_key not in raw_prefs:
            raw_prefs = {**raw_prefs, legacy_key: s[legacy_key]}
    prefs = dict(USER_PREFERENCES)
    theme = str(raw_prefs.get("theme", prefs["theme"]) or "").strip().lower()
    prefs["theme"] = "dark" if theme == "dark" else "light"
    after = str(raw_prefs.get("after_export", prefs["after_export"]) or "").strip().lower()
    prefs["after_export"] = after if after in ("copy", "open", "reveal", "none") else "copy"
    prefs["compact_tables"] = _pref_bool(raw_prefs.get("compact_tables"), prefs["compact_tables"])
    prefs["dn_prices_default"] = _pref_bool(raw_prefs.get("dn_prices_default"), prefs["dn_prices_default"])
    USER_PREFERENCES = prefs

    # ---- PO per-line company classification (name -> "client"|"internal") ----
    # 2026-09-05 - read through the declaration like the rest of the PO block.
    # This read the TOP-LEVEL key only for one release after the panel had
    # already moved to `purchase_order.company_tags`, so edited tags landed in
    # settings.json and never reached this global: saved, reloaded, invisible -
    # the exact failure the unification exists to make impossible.
    raw_tags = _po_val("PO_COMPANY_TAGS")
    if isinstance(raw_tags, dict):
        PO_COMPANY_TAGS = {
            str(k).strip(): str(v).strip().lower()
            for k, v in raw_tags.items()
            if str(k).strip() and str(v).strip().lower() in ("client", "internal")
        }

    # ---- per-doc-type sections ----
    def _merge_company(base: dict, override: dict) -> dict:
        merged = {**base}
        for k, v in (override or {}).items():
            if v is not None:
                merged[k] = v
        return merged

    global QUOTE_LAYOUT, DN_LAYOUT, INV_LAYOUT
    global QUOTE_SUBTYPES_OVERRIDES, INV_SUBTYPES_OVERRIDES
    QUOTE_LAYOUT = {}
    DN_LAYOUT    = {}
    INV_LAYOUT   = {}
    QUOTE_SUBTYPES_OVERRIDES = {}    # {subtype_key: {terms, column_heads, layout}}
    INV_SUBTYPES_OVERRIDES   = {}

    def _layout_dict(raw):
        # 2026-07-30 - this whitelist decides which spacing settings survive the
        # trip from settings.json into the generators. It had drifted out of sync
        # with what the generators actually read, in BOTH directions:
        #   * `max_row_mm` / `image_row_min_mm` were read by the generators but
        #     dropped here, so the settings could never reach them;
        #   * `max_items` / `budget_mm` were kept here but read by nobody, so the
        #     settings UI offered two knobs that silently did nothing.
        # They are still accepted (old settings.json files carry them and it
        # costs nothing) but the UI no longer offers them - the page-role engine
        # derives the per-page budget from measured geometry, and a hard item cap
        # fights content-aware pagination, so neither can be honoured as written.
        if not isinstance(raw, dict): return {}
        out = {}
        for k in ("max_items", "budget_mm", "min_row_mm", "safe_gap_mm",
                  "max_row_mm", "image_row_min_mm"):
            v = raw.get(k)
            if v is None: continue
            try: out[k] = float(v)
            except (TypeError, ValueError): pass
        # Auto-fit row height (boolean) - each row hugs its text in the PDF.
        if raw.get("autofit_rows"):
            out["autofit_rows"] = True
        return out

    def _subtype_overrides(sec, valid_keys):
        """Extract {key: {terms, column_heads, columns, layout}} for sub-types only.

        Each value is stored as a typed dict - non-recognised fields are dropped.
        Empty subtype dicts are skipped so they don't shadow defaults.

        `columns` (Phase G.4): full ordered column list replacing the default.
        Each entry is {key, head, kind, width?}. Widths optional - auto-balanced
        at render time. Allowed kinds: text, num, money, computed, image, index.
        """
        ALLOWED_KINDS = {"text", "num", "money", "computed", "image", "index"}
        out = {}
        for k in valid_keys:
            sub = sec.get(k)
            if not isinstance(sub, dict): continue
            entry = {}
            if isinstance(sub.get("terms"), list):
                entry["terms"] = [str(t) for t in sub["terms"] if str(t).strip()]
            if isinstance(sub.get("column_heads"), dict):
                entry["column_heads"] = {
                    str(ck): str(cv) for ck, cv in sub["column_heads"].items()
                    if str(cv).strip()
                }
            if isinstance(sub.get("columns"), list):
                cols = []
                for c in sub["columns"]:
                    if not isinstance(c, dict): continue
                    key  = str(c.get("key", "")).strip()
                    head = str(c.get("head", "")).strip()
                    kind = str(c.get("kind", "text")).strip().lower()
                    if not key or not head: continue
                    if kind not in ALLOWED_KINDS: kind = "text"
                    col = {"key": key, "head": head, "kind": kind}
                    try:
                        if c.get("width") not in (None, ""):
                            col["width"] = float(c["width"])
                    except (TypeError, ValueError):
                        pass
                    if c.get("default"):
                        col["default"] = str(c["default"])
                    cols.append(col)
                if cols:
                    entry["columns"] = cols
            lay = _layout_dict(sub.get("layout"))
            if lay:
                entry["layout"] = lay
            if entry:
                out[k] = entry
        return out

    qsec = s.get("quotation") or {}
    if isinstance(qsec, dict):
        if "brand_color" in qsec and qsec["brand_color"]:
            QUOTE_BRAND_COLOR = str(qsec["brand_color"]).strip()
        if "company" in qsec and isinstance(qsec["company"], dict):
            QUOTE_COMPANY = _merge_company(QUOTE_COMPANY, qsec["company"])
        if "address_options" in qsec and isinstance(qsec["address_options"], list):
            QUOTE_ADDRESS_OPTIONS = [str(x) for x in qsec["address_options"] if str(x).strip()]
        QUOTE_LAYOUT = _layout_dict(qsec.get("layout"))
        QUOTE_SUBTYPES_OVERRIDES = _subtype_overrides(
            qsec, list(QUOTE_TYPES.keys()))

    dsec = s.get("delivery") or {}
    if isinstance(dsec, dict):
        if "brand_color" in dsec and dsec["brand_color"]:
            DN_BRAND_COLOR = str(dsec["brand_color"]).strip()
        if "company" in dsec and isinstance(dsec["company"], dict):
            DN_COMPANY = _merge_company(DN_COMPANY, dsec["company"])
        if "address_options" in dsec and isinstance(dsec["address_options"], list):
            DN_ADDRESS_OPTIONS = [str(x) for x in dsec["address_options"] if str(x).strip()]
        DN_LAYOUT = _layout_dict(dsec.get("layout"))
        # Phase G.7: optional columns override for the DN base table. Price
        # columns (Rate, Total) are NOT included here - they're appended at
        # render time when include_prices is True.
        global DN_BASE_COLUMNS_OVERRIDE
        DN_BASE_COLUMNS_OVERRIDE = []
        if isinstance(dsec.get("columns"), list):
            ALLOWED = {"text", "num", "money", "computed", "image", "index"}
            for c in dsec["columns"]:
                if not isinstance(c, dict): continue
                key  = str(c.get("key", "")).strip()
                head = str(c.get("head", "")).strip()
                kind = str(c.get("kind", "text")).strip().lower()
                if not key or not head: continue
                if kind not in ALLOWED: kind = "text"
                col = {"key": key, "head": head, "kind": kind}
                try:
                    if c.get("width") not in (None, ""):
                        col["width"] = float(c["width"])
                except (TypeError, ValueError):
                    pass
                DN_BASE_COLUMNS_OVERRIDE.append(col)

    isec = s.get("invoice") or {}
    if isinstance(isec, dict):
        if "brand_color" in isec and isec["brand_color"]:
            INV_BRAND_COLOR = str(isec["brand_color"]).strip()
        if "company" in isec and isinstance(isec["company"], dict):
            INV_COMPANY = _merge_company(INV_COMPANY, isec["company"])
        if "address_options" in isec and isinstance(isec["address_options"], list):
            INV_ADDRESS_OPTIONS = [str(x) for x in isec["address_options"] if str(x).strip()]
        if "withholding_pct" in isec:
            try: INV_WITHHOLDING_PCT = float(isec["withholding_pct"])
            except (TypeError, ValueError): pass
            # Keep the user-visible tax label in sync if the user didn't override it.
            INV_COMPANY = _merge_company(INV_COMPANY, {
                "tax_label": f"Withholding Tax ({INV_WITHHOLDING_PCT} %)"
            })
        INV_LAYOUT = _layout_dict(isec.get("layout"))
        INV_SUBTYPES_OVERRIDES = _subtype_overrides(
            isec, list(INV_TYPES.keys()))
        if "bank_info" in isec and isinstance(isec["bank_info"], list):
            new_banks = []
            for b in isec["bank_info"]:
                if not isinstance(b, dict): continue
                new_banks.append({
                    "key":          str(b.get("key", "") or "").strip() or f"bank{len(new_banks)+1}",
                    "label":        str(b.get("label", "") or ""),
                    "company_line": str(b.get("company_line", "") or ""),
                    "bank_name":    str(b.get("bank_name", "") or ""),
                    "branch":       str(b.get("branch", "") or ""),
                    "swift":        str(b.get("swift", "") or ""),
                    "account_no":   str(b.get("account_no", "") or ""),
                    "iban":         str(b.get("iban", "") or ""),
                    "currency":     str(b.get("currency", "") or "USD"),
                    # 2026-09-21 - this account's IQD-per-USD rate, which is
                    # what a document paid into it costs at. This rebuild is a
                    # WHITELIST: a key missing from it is silently discarded on
                    # load, however correctly it was saved. That is exactly how
                    # the rate typed in Settings > Payments did nothing.
                    "iqd_per_usd":  _bank_rate(b.get("iqd_per_usd")),
                })
            if new_banks:
                INV_BANK_INFO = new_banks
        # The rate for a document whose client has no receiving account
        # (2026-09-21). One visible number rather than a constant in code.
        _fb = _bank_rate(isec.get("fallback_iqd_per_usd"))
        if _fb is not None:
            INV_FALLBACK_IQD_PER_USD = _fb


def save_user_settings(settings: dict) -> None:
    """Write the settings dict to data/settings.json (creates parents)."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def po_company_is_internal(name: str) -> bool:
    """True only when `name` is explicitly tagged "internal" (our own office) in
    PO_COMPANY_TAGS. Case/space-insensitive. An untagged or "client" name → False
    (treated as billable by the To-Do). Blank → False."""
    key = (name or "").strip().casefold()
    if not key:
        return False
    for n, t in (PO_COMPANY_TAGS or {}).items():
        if str(n).strip().casefold() == key:
            return str(t).strip().lower() == "internal"
    return False


def current_effective_settings() -> dict:
    """Snapshot of every tunable value as it currently stands."""
    return {
        "profile_id": ACTIVE_PROFILE,
        "logo_data_uri": profile_manager.logo_ui_data_uri(),   # display size; PDFs read the file
        "documents_root": str(DOCUMENTS_ROOT),
        "po_root": str(PO_ROOT),
        "ui_lang": UI_LANG,
        "po_prefix": PO_PREFIX,
        "autofit_rows": PO_AUTOFIT,
        "table_header_mm": TABLE_HEADER_MM,
        "table_min_row_mm": TABLE_MIN_ROW_MM,
        "table_line_height_mm": TABLE_LINE_HEIGHT_MM,
        "po_signature_mm": PO_SIGNATURE_MM,
        "brand_color": BRAND_COLOR,
        "preferences": dict(USER_PREFERENCES),
        "po_company_tags": dict(PO_COMPANY_TAGS),
        "company": dict(COMPANY),
        # Per-doc-type sections (each can be edited independently).
        # 2026-09-05 - the PO joined them. The flat keys above are the SAME
        # values under their old spellings: the App-settings screen and any
        # settings.json written before the move still read them, and dropping
        # them would break an older backup on restore. `purchase_order` is what
        # the doc-settings panel and `save_doc_settings` use.
        "purchase_order": {
            "prefix":       PO_PREFIX,
            "brand_color":  BRAND_COLOR,
            "company":      dict(COMPANY),
            "company_tags": dict(PO_COMPANY_TAGS),
            "layout": {
                "autofit_rows":         PO_AUTOFIT,
                "table_header_mm":      TABLE_HEADER_MM,
                "table_min_row_mm":     TABLE_MIN_ROW_MM,
                "table_line_height_mm": TABLE_LINE_HEIGHT_MM,
                "signature_mm":         PO_SIGNATURE_MM,
            },
        },
        "quotation": {
            "brand_color":     QUOTE_BRAND_COLOR,
            "company":         dict(QUOTE_COMPANY),
            "address_options": list(QUOTE_ADDRESS_OPTIONS),
            "layout":          dict(QUOTE_LAYOUT),
            # Per-sub-type overrides exposed as-is so the UI can render
            # "<TypeName> specific" sections.
            **{k: dict(v) for k, v in QUOTE_SUBTYPES_OVERRIDES.items()},
        },
        "delivery": {
            "brand_color":     DN_BRAND_COLOR,
            "company":         dict(DN_COMPANY),
            "address_options": list(DN_ADDRESS_OPTIONS),
            "layout":          dict(DN_LAYOUT),
            # Phase G.7 - surface the active base-columns shape so the
            # settings modal can prefill its editor. Falls through to the
            # built-in DN_BASE_COLUMNS when no override is in place.
            "columns":         [dict(c) for c in (DN_BASE_COLUMNS_OVERRIDE or DN_BASE_COLUMNS)],
        },
        "invoice": {
            "brand_color":     INV_BRAND_COLOR,
            "company":         dict(INV_COMPANY),
            "address_options": list(INV_ADDRESS_OPTIONS),
            "withholding_pct": INV_WITHHOLDING_PCT,
            "bank_info":       [dict(b) for b in INV_BANK_INFO],
            # 2026-09-21 - the Payments tab prefills its fallback box from
            # here. Omit it and the box reads blank however it was saved,
            # which is indistinguishable from a setting that never saved.
            "fallback_iqd_per_usd": INV_FALLBACK_IQD_PER_USD,
            "layout":          dict(INV_LAYOUT),
            **{k: dict(v) for k, v in INV_SUBTYPES_OVERRIDES.items()},
        },
    }


# Apply overlay at import time.
_apply_user_settings()
