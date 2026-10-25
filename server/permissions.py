"""Central role-to-capability policy for the office gateway."""
from __future__ import annotations

ROLE_PERMISSIONS = {
    "admin": frozenset({
        "users.view", "users.manage", "sessions.manage", "server.view",
        "audit.view", "documents.all", "master_data.all", "reports.all",
    }),
    "accountant": frozenset({"documents.accounting", "reports.accounting"}),
    "procurement": frozenset({"documents.procurement", "master_data.procurement"}),
}


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(str(role or "").lower(), ()))


def allows(role: str, permission: str) -> bool:
    granted = ROLE_PERMISSIONS.get(str(role or "").lower(), ())
    return permission in granted or "documents.all" in granted
