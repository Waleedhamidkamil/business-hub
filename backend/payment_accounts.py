"""Which payment account a document is costed against, and at what rate.

One client is paid into one account, and that account has its own IQD-per-USD
rate: Bank of Baghdad at 1400, ADIB at 1500. The rate a document's COSTS convert
at follows the account the money actually arrives in.

This is deliberately the only place that answers that question. Before
2026-09-21 the answer was spread across two invisible rules inside
`database.doc_iqd_per_usd` - "Eshraqat is always 1500" and "a deal paid in cash
is 1500" - which produced a number on the Costing screen with no way to see why.
Cash is now simply a payment method with a rate anybody can read and edit.

Scope: the PROFIT maths only. What an invoice BILLS the client at is
`invoices.sell_iqd_per_usd` and is none of this module's business. A deal may
legitimately be costed at one rate and billed at another.
"""
from __future__ import annotations

from typing import Any

from . import config

# Set by tests to stand in for the profile's settings. None in the real app.
_accounts_override: list[dict[str, Any]] | None = None

# The rate used when no account can be determined. Waleed, 2026-09-21: "1500".
# Overridable per profile through `invoice.fallback_iqd_per_usd`, so it is a
# number somebody can see and change rather than one buried in code - which is
# exactly how the two rules this replaces came to be invisible.
DEFAULT_FALLBACK = 1500.0


def _num(value: Any) -> float | None:
    """A usable rate, or None. Zero and negatives are not rates."""
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if rate > 0 else None


def accounts() -> list[dict[str, Any]]:
    """The active profile's payment methods."""
    if _accounts_override is not None:
        return list(_accounts_override)
    banks = getattr(config, "INV_BANK_INFO", None) or []
    return [b for b in banks if isinstance(b, dict)]


def fallback_rate() -> float:
    """The rate for a document with no account behind it."""
    return _num(getattr(config, "INV_FALLBACK_IQD_PER_USD", None)) or DEFAULT_FALLBACK


def find_account(key: str) -> dict[str, Any] | None:
    """The account with this key, matched case-insensitively.

    settings.json holds "ADIB" while a client record may hold "adib". They have
    always meant one account, and a lookup that says otherwise silently changes
    a rate.
    """
    want = str(key or "").strip().casefold()
    if not want:
        return None
    for account in accounts():
        if str(account.get("key") or "").strip().casefold() == want:
            return account
    return None


def rate_for_account(key: str) -> float:
    """This account's rate, or the fallback when it has none."""
    account = find_account(key)
    if not account:
        return fallback_rate()
    return _num(account.get("iqd_per_usd")) or fallback_rate()


def client_account(client_name: str) -> str:
    """The account this client is paid into, from Clients Master Data.

    Resolved through the identity index, so `HDEC` and `Hyundai Engineering and
    Construction Co., Ltd` reach the same record. A client with no record, or an
    unreadable client store, returns "" rather than raising: the Costing screen
    runs on every keystroke and losing it is worse than losing a rate.
    """
    name = str(client_name or "").strip()
    if not name:
        return ""
    try:
        from . import client_master
        found = client_master.get_record(name) or {}
    except Exception:
        return ""
    record = found.get("record") or {}
    defaults = record.get("defaults") or {}
    return str(defaults.get("bank_key") or "").strip()


def account_for_document(kind: str, client_name: str = "",
                         bank_key: str = "") -> str:
    """Which account this document is costed against.

    An INVOICE names its own account and that wins: it is the account receiving
    the money, even when it differs from the client's usual one. A quotation or
    delivery note has no account of its own, so the client's stands.
    """
    chosen = str(bank_key or "").strip()
    if chosen and find_account(chosen):
        return chosen
    return client_account(client_name)


def rate_for_document(kind: str, client_name: str = "",
                      bank_key: str = "") -> float:
    """The rate this document's costs convert at, resolved live.

    For a SAVED document use `database.doc_iqd_per_usd`, which reads the stamp
    instead: the rate is frozen at save so it cannot move afterwards.
    """
    return rate_for_account(account_for_document(kind, client_name, bank_key))
