# Office Management design

## Goal

Provide administrators with one integrated Business Hub area for managing office
accounts and observing the office server, without turning the product into a
Windows administration tool.

## Scope

Version one covers:

- account listing and creation;
- display name, role, enabled state, and password reset;
- a central permission vocabulary used by the gateway and backend;
- forced logout/session revocation;
- connected PC and engine health visibility;
- audit history through the existing activity log.

Windows services, firewall rules, Python/Chrome/font installation, backups,
HTTPS certificates, and scheduled startup remain in the server setup utility.

## Architecture

The gateway remains the security boundary. Administrative endpoints operate
there, use the existing `UserStore` and `Sessions` stores, and require an
authenticated administrator. User-facing document methods continue to route to
per-user engines. The UI must not be the authorization mechanism: hidden
buttons are only a convenience, while gateway and engine checks reject
unauthorized calls.

Permissions are named capabilities rather than hard-coded UI roles. The initial
roles map to capabilities for document areas, master data, reports, activity
log, user management, server status, and session control. Existing role names
remain compatible; missing permission data is migrated to the documented
defaults.

## Data flow

The Admin screen requests a summary from the gateway. Account mutations are
validated, written atomically, logged, and cause affected sessions to be
revoked when required. Server status is derived from the gateway process,
engine pool state, disk availability, and recent activity; it does not expose
secrets, passwords, document contents, or private assets.

## Error handling

Invalid names, duplicate accounts, weak passwords, attempts to disable the last
administrator, and unauthorized calls return explicit errors. Mutations are
audited with actor, target, PC, action, outcome, and technical detail. Partial
success is not reported as success.

## UI

Add an Admin -> Office Management entry visible only to administrators. Use
three tabs: Accounts, Server, and Audit. Accounts presents status and role
filters plus create/edit/password/session actions. Server presents gateway,
disk, engine, and connected-PC health. Audit links to the existing activity
history with administrator filters. Destructive actions require confirmation
and explain their effect.

## Testing and rollout

Add tests for permission mapping, admin-only endpoint access, account
validation, last-admin protection, password reset, session revocation, and
non-leakage of secrets. Keep the existing login and gateway tests passing.
Deploy the gateway changes before enabling the navigation entry; old clients
must continue to work when the new endpoints are unavailable.

## Explicit exclusions

Version one does not include Windows service control, remote shell commands,
backup/restore, HTTPS provisioning, per-document custom roles, or a separate
administration application.
