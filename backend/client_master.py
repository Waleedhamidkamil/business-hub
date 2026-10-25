"""Client identity and preferences. Deliberately independent of material storage."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import threading

from . import config, db_version, profile_manager, shared_library, sqlite_policy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_records (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
 archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_aliases (
 alias_key TEXT PRIMARY KEY, alias TEXT NOT NULL, client_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_client_alias_owner ON client_aliases(client_id);
CREATE TABLE IF NOT EXISTS client_preferences (
 client_id TEXT NOT NULL, profile_id TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(client_id,profile_id)
);
CREATE TABLE IF NOT EXISTS client_master_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
_TEXT_FIELDS = ("legal_name", "name_ar", "website", "tax_id", "registration_no", "notes")

# WHICH SPELLINGS IDENTIFY A CLIENT (2026-09-11).
#
# The alias table is the only resolution index, so a name that is not in it
# cannot be recognised. Until now it held the record name and the explicit
# aliases only - which meant the LEGAL name and the ARABIC name, both already
# stored on the record, resolved to nothing. Typing the Iraqi branch's full
# legal name on an invoice, or the Arabic name on a purchase order, produced a
# stranger: no client code for the number and no payment defaults.
#
# These are identities, not aliases: they stay out of the editable alias list
# (they have their own fields) but they resolve like one.
_IDENTITY_FIELDS = ("legal_name", "name_ar")


def identity_names(name, details):
    """Every spelling that MEANS this client, most canonical first."""
    out = [str(name or "").strip()]
    for field in _IDENTITY_FIELDS:
        value = str((details or {}).get(field) or "").strip()
        if value:
            out.append(value)
    return [v for v in out if v]


def normalize(value):
    return " ".join(str(value or "").strip().split()).casefold()

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")

def _text(value, label, limit=1000):
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{label} is too long (maximum {limit} characters).")
    return value

def _alias(c, value):
    return c.execute("SELECT r.* FROM client_records r JOIN client_aliases a "
                     "ON a.client_id=r.id WHERE a.alias_key=?", (normalize(value),)).fetchone()

def _find(c, identity):
    return c.execute("SELECT * FROM client_records WHERE id=?", (str(identity),)).fetchone() or _alias(c, identity)

def _insert(c, name):
    cid, now = uuid.uuid4().hex, _now()
    c.execute("INSERT INTO client_records(id,name,created_at,updated_at) VALUES(?,?,?,?)",
              (cid, name, now, now))
    c.execute("INSERT INTO client_aliases VALUES(?,?,?)", (normalize(name), name, cid))
    return cid

def _init(c):
    c.executescript(_SCHEMA)
    _sync(c)


def _sync(c):
    # Import catalogue names once per identity, including names remembered by
    # older app instances. An existing alias always owns its old spelling.
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='clients'").fetchone():
        for row in c.execute("SELECT name FROM clients").fetchall():
            name = str(row[0] or "").strip()
            if name and not _alias(c, name):
                _insert(c, name)
    _index_identity_names(c)
    c.commit()


def _index_identity_names(c):
    """Make every record's legal and Arabic name resolvable (2026-09-11).

    Records saved before this existed have those names in `details_json` but
    not in the alias index, so they resolved to nothing. Idempotent and cheap -
    it inserts only what is missing, and never steals a spelling that another
    client already owns.
    """
    try:
        rows = c.execute("SELECT id,name,details_json FROM client_records").fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for spelling in identity_names(row["name"], details):
            key = normalize(spelling)
            if not key:
                continue
            owner = c.execute("SELECT client_id FROM client_aliases WHERE alias_key=?",
                              (key,)).fetchone()
            if owner is None:
                c.execute("INSERT INTO client_aliases VALUES(?,?,?)",
                          (key, spelling, row["id"]))
            # An existing owner is left alone: two clients claiming one
            # spelling is a question for a person, not something to guess at.

# SETUP RUNS WHEN THE DATA CHANGED, NOT ON EVERY CONNECTION (2026-10-01).
#
# `_init` used to run in full on every `_connection()`: the schema script, a
# scan of the legacy `clients` table, a re-index of every record's legal and
# Arabic names (one SELECT per spelling), and a commit - about 4 ms each time.
# Needs Attention resolves 359 client names on the way to the hub, so returning
# to the hub spent 1.3 of its 1.4 seconds redoing setup that had nothing new to
# do. Waleed felt that as a two-second wait every time he went home.
#
# Two halves, kept correct separately:
#   * the SCHEMA is idempotent and only needs running once per database file
#     per process,
#   * the SYNC is what turns a client name typed on a saved document (written
#     to the legacy `clients` table by database.remember_client) into a real
#     client record. It still has to happen after every such write - so it is
#     gated on `db_version.version`, SQLite's own "did anyone commit since"
#     counter. Any commit by any connection or process re-runs it; nothing
#     else does. An unreadable counter (None) always re-runs it.
_setup_lock = threading.Lock()
_schema_done: set[str] = set()
_synced_at: dict[str, str] = {}


def _db_file(c):
    """The file this connection really has open.

    Keyed on the connection rather than on `profile_manager.shared_db_path()`
    because tests swap `shared_library.connect` for a temporary database; a key
    taken from the profile manager would then mark the LIVE file as set up
    while the temporary one never got its schema.
    """
    for row in c.execute("PRAGMA database_list"):
        if row[1] == "main":
            return row[2] or None
    return None


def _prepare(c):
    path = _db_file(c)
    if not path:                       # in-memory or unknown: no caching at all
        _init(c)
        return
    key = str(path)
    with _setup_lock:
        # The fingerprint is read BEFORE the work it guards. If another
        # process commits while we sync, the stored value is the older one and
        # the next call syncs again; reading it after would file their commit
        # as already synced and never import it. Our own sync's writes move the
        # fingerprint too, which costs exactly one more (empty) sync.
        v = db_version.version(path)
        if key not in _schema_done:
            _init(c)
            _schema_done.add(key)
        elif v is not None and _synced_at.get(key) == v:
            return
        else:
            _sync(c)
        if v is not None:
            _synced_at[key] = v
        else:
            _synced_at.pop(key, None)


@contextmanager
def _connection():
    c = shared_library.connect()
    try:
        _prepare(c)
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def seed_once(names):
    with _connection() as c:
        if c.execute("SELECT 1 FROM client_master_meta WHERE key='presets_seeded'").fetchone():
            return
        for name in names:
            name = str(name or "").strip()
            if not name:
                continue
            c.execute("INSERT OR IGNORE INTO clients(name,use_count,last_used_at) VALUES(?,0,?)", (name,_now()))
            if not _alias(c,name):
                _insert(c,name)
        c.execute("INSERT INTO client_master_meta VALUES('presets_seeded',?)",(_now(),))

def bank_options():
    return [{"key":str(b["key"]), "label":str(b.get("label") or b["key"]),
             "currency":str(b.get("currency") or "USD")}
            for b in getattr(config,"INV_BANK_INFO",[]) if isinstance(b,dict) and b.get("key")]

def _aliases(c, row):
    return [a[0] for a in c.execute("SELECT alias FROM client_aliases WHERE client_id=? ORDER BY alias",
                                   (row["id"],))]

def usage_index():
    """Every place a client name is used, counted ONCE for all clients (2026-10-01).

    `usage_for` used to scan every quotation, delivery note, invoice, LPO and
    every PO line in BOTH companies - parsing each PO's rows - once per client.
    Master Data > Clients asks for every client, so the list's cost grew with
    clients x documents (300 ms for 6 clients on the live data, and climbing
    with every document saved). This walks the same tables once and counts by
    normalised name; `usage_for(names, index)` then just adds up its aliases.

    The walk is the old one, statement for statement, so the counts, the
    reference keys, their order and the error messages are unchanged -
    including the partial counts a profile keeps when it fails half way.
    """
    from collections import Counter
    profiles = []
    for profile in profile_manager.list_profiles():
        pid = profile["id"]
        path = profile_manager.profile_db_path(pid)
        entry = {"pid": pid, "missing": False, "error": None, "tables": [], "po": None}
        profiles.append(entry)
        if not path.is_file():
            # Missing storage is not evidence that a client is unused.
            entry["missing"] = True
            continue
        c = None
        try:
            c = sqlite_policy.open_db(path.resolve().as_uri()+"?mode=ro", uri=True, row_factory=False)
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("quotes","dns","invoices","client_lpos"):
                if table not in tables: continue
                entry["tables"].append((table, Counter(normalize(r[0]) for r in
                                                       c.execute(f"SELECT client_name FROM {table}"))))
            if "pos" in tables:
                po=Counter()
                for r in c.execute("SELECT rows_json FROM pos"):
                    for line in json.loads(r[0] or "[]"):
                        if isinstance(line,dict):po[normalize(line.get("company"))]+=1
                entry["po"]=po
        except (sqlite3.Error, ValueError, TypeError) as exc:
            entry["error"] = f"{pid}: {exc}"
        finally:
            if c is not None:c.close()
    shared, shared_error = [], None
    c=None
    try:
        c=shared_library.connect()
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "master_items" in tables:
            cols={r[1] for r in c.execute("PRAGMA table_info(master_items)")}
            if "last_company" in cols:
                shared.append(("materials.company_memory", Counter(
                    normalize(r[0]) for r in c.execute("SELECT last_company FROM master_items"))))
        if "column_memory" in tables:
            shared.append(("column_memory", Counter(
                normalize(r[0]) for r in c.execute("SELECT value FROM column_memory"))))
    except sqlite3.Error as exc:shared_error=str(exc)
    finally:
        if c is not None:c.close()
    return {"profiles": profiles, "shared": shared, "shared_error": shared_error}


def usage_for(names, index=None):
    """Counts real dependencies across profiles. Never creates a profile file.

    Pass `index` (from `usage_index()`) when asking about many clients."""
    keys = {normalize(n) for n in names}
    if index is None:
        index = usage_index()
    by_profile, refs, errors = {}, {}, []
    for entry in index["profiles"]:
        pid = entry["pid"]
        if entry["missing"]:
            errors.append(f"{pid}: profile database unavailable")
            continue
        total=0
        for table, counts in entry["tables"]:
            n=sum(counts[k] for k in keys)
            if n: refs[f"{pid}.{table}"]=n
            total+=n
        if entry["error"]:
            errors.append(entry["error"])
            continue
        if entry["po"] is not None:
            n=sum(entry["po"][k] for k in keys)
            if n:refs[f"{pid}.po_allocations"]=n
        by_profile[pid]=total
    for label, counts in index["shared"]:
        n=sum(counts[k] for k in keys)
        if n:refs[label]=n
    if index["shared_error"]:errors.append(index["shared_error"])
    return {"total":sum(by_profile.values()),"by_profile":by_profile,"references":refs,"errors":errors}

def _out(c,row,with_usage=True):
    details=json.loads(row["details_json"])
    defaults=c.execute("SELECT data_json FROM client_preferences WHERE client_id=? AND profile_id=?",
                       (row["id"],profile_manager.active_profile_id())).fetchone()
    aliases=_aliases(c,row)
    out={**{k:"" for k in _TEXT_FIELDS},"contacts":[],"addresses":[],**details,
         "id":row["id"],"name":row["name"],"archived":bool(row["archived"]),
         # The record name, the legal name and the Arabic name all RESOLVE,
         # but they have their own fields; listing them again as editable
         # aliases would round-trip duplicates into the alias list on save.
         "aliases":[a for a in aliases
                    if normalize(a) not in {normalize(n) for n
                                            in identity_names(row["name"],details)}],
         "created_at":row["created_at"],"updated_at":row["updated_at"],
         "defaults":json.loads(defaults[0]) if defaults else {}}
    if with_usage:out["usage"]=usage_for(aliases)
    return out

def get_record(identity):
    if not identity:
        return {"ok":True,"record":None,"bank_options":bank_options(),
                "profile_id":profile_manager.active_profile_id()}
    with _connection() as c:
        row=_find(c,identity)
        if not row:return {"ok":False,"error":"Client not found."}
        return {"ok":True,"record":_out(c,row),"bank_options":bank_options(),
                "profile_id":profile_manager.active_profile_id()}

def list_records(search="",status="active"):
    if status not in ("active","archived","all"):
        return {"ok":False,"error":"Unknown client status."}
    with _connection() as c:
        rows=c.execute("SELECT * FROM client_records ORDER BY name COLLATE NOCASE").fetchall()
        out=[]
        index=None                      # built on the first record that needs it
        for row in rows:
            if status!="all" and bool(row["archived"])!=(status=="archived"):continue
            record=_out(c,row,False)
            if search and normalize(search) not in normalize(json.dumps(record,ensure_ascii=False)):continue
            if index is None:index=usage_index()
            record["usage"]=usage_for(_aliases(c,row),index)
            out.append(record)
        return {"ok":True,"records":out,"total":len(out)}

def _validate_lists(payload,details):
    if "contacts" in payload:
        contacts=payload["contacts"]
        if not isinstance(contacts,list) or len(contacts)>30:raise ValueError("Use up to 30 contacts.")
        result=[]
        for item in contacts:
            if not isinstance(item,dict):raise ValueError("Invalid contact.")
            entry={k:_text(item.get(k,""),k) for k in ("name","role","email","phone")}
            if not isinstance(item.get("primary",False),bool):raise ValueError("Primary must be true or false.")
            entry["primary"]=item.get("primary",False)
            if any(entry[k] for k in ("name","role","email","phone")):result.append(entry)
        if sum(e["primary"] for e in result)>1:raise ValueError("Choose one primary contact.")
        details["contacts"]=result
    if "addresses" in payload:
        addresses=payload["addresses"]
        if not isinstance(addresses,list) or len(addresses)>30:raise ValueError("Use up to 30 addresses.")
        details["addresses"]=[]
        for item in addresses:
            if not isinstance(item,dict):raise ValueError("Invalid address.")
            entry={k:_text(item.get(k,""),k,4000) for k in ("label","address")}
            if entry["address"]:details["addresses"].append(entry)

def _preferences(incoming,current):
    if not isinstance(incoming,dict):raise ValueError("Invalid client preferences.")
    out=dict(current)
    for key in ("payment_method","bank_key","currency","invoice_instructions","address"):
        if key in incoming:out[key]=_text(incoming[key],key,10000)
    if out.get("payment_method","") not in ("","cash","bank_transfer"):raise ValueError("Unknown payment method.")
    if out.get("currency","") not in ("","USD","IQD"):raise ValueError("Unknown currency.")
    if "alternative_bank_keys" in incoming:
        values=incoming["alternative_bank_keys"]
        if not isinstance(values,list) or len(values)>30:raise ValueError("Invalid alternative accounts.")
        out["alternative_bank_keys"]=list(dict.fromkeys(_text(x,"account key") for x in values if x))
    banks={b["key"] for b in bank_options()}
    # A bank that was deleted from settings can stay visible until the user
    # edits preferences, but cannot be silently selected for a new document.
    for key in [out.get("bank_key","")]+out.get("alternative_bank_keys",[]):
        if key and key not in banks:raise ValueError("An account is unavailable in this company profile. Select another account.")
    if out.get("payment_method")=="bank_transfer" and out.get("bank_key","").casefold()=="cash":
        raise ValueError("Bank transfer needs a bank account. Choose Cash as the payment method for cash payments.")
    if "payment_terms_days" in incoming:
        days=incoming["payment_terms_days"]
        if days is not None and (type(days) is not int or not 0<=days<=3650):
            raise ValueError("Payment terms must be between 0 and 3650 days.")
        out["payment_terms_days"]=days
    if "withholding" in incoming:
        val=incoming["withholding"]
        if val is not None and type(val) is not bool:raise ValueError("Invalid withholding preference.")
        out["withholding"]=val
    return out

def save_record(payload):
    try:
        if not isinstance(payload,dict):raise ValueError("Invalid client record.")
        pid=profile_manager.active_profile_id()
        if payload.get("profile_id",pid)!=pid:raise ValueError("Company profile changed. Reload this client before saving.")
        with _connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row=_find(c,payload["id"]) if payload.get("id") else None
            if payload.get("id") and not row:raise ValueError("Client not found.")
            if row and payload.get("updated_at") and payload["updated_at"]!=row["updated_at"]:
                raise ValueError("This client changed since it was opened. Reload before saving.")
            name=_text(payload.get("name",row["name"] if row else ""),"Client name",250)
            if not name:raise ValueError("Client name is required.")
            # The identity names come out of `details`, so resolve those first.
            details_preview=json.loads(row["details_json"]) if row else {}
            for field in _TEXT_FIELDS:
                if field in payload:
                    details_preview[field]=_text(payload[field],field,
                                                 10000 if field=="notes" else 1000)
            old_aliases=_aliases(c,row) if row else []
            aliases=payload.get("aliases",old_aliases)
            if not isinstance(aliases,list) or len(aliases)>100:raise ValueError("Aliases must be a list of up to 100 names.")
            # Always retain old names used on historical documents.
            # The legal and Arabic names resolve too - see identity_names.
            names=identity_names(name,details_preview)+[_text(a,"Alternative name",250) for a in aliases]
            if row and normalize(name)!=normalize(row["name"]):names.append(row["name"])
            for old in old_aliases:
                if normalize(old) not in {normalize(n) for n in names}:
                    use=usage_for([old])
                    if use["references"] or use["errors"]:names.append(old)
            names=list({normalize(n):n for n in names if normalize(n)}.values())
            for alias in names:
                owner=_alias(c,alias)
                if owner and (not row or owner["id"]!=row["id"]):
                    raise ValueError(f'Alternative name "{alias}" already belongs to {owner["name"]}.')
            details=details_preview
            _validate_lists(payload,details)
            archived=payload.get("archived",bool(row["archived"]) if row else False)
            if type(archived) is not bool:raise ValueError("Invalid archive status.")
            cid=row["id"] if row else uuid.uuid4().hex
            pref=c.execute("SELECT data_json FROM client_preferences WHERE client_id=? AND profile_id=?",(cid,pid)).fetchone()
            defaults=_preferences(payload["defaults"],json.loads(pref[0]) if pref else {}) if "defaults" in payload else None
            now=_now()
            if row:
                c.execute("UPDATE client_records SET name=?,details_json=?,archived=?,updated_at=? WHERE id=?",
                          (name,json.dumps(details,ensure_ascii=False),int(archived),now,cid))
            else:
                c.execute("INSERT INTO client_records VALUES(?,?,?,?,?,?)",
                          (cid,name,json.dumps(details,ensure_ascii=False),int(archived),now,now))
            removed_keys={normalize(a) for a in old_aliases}-{normalize(a) for a in names}
            for legacy in c.execute("SELECT name FROM clients").fetchall():
                if normalize(legacy[0]) in removed_keys:
                    c.execute("DELETE FROM clients WHERE name=?",(legacy[0],))
            c.execute("DELETE FROM client_aliases WHERE client_id=?",(cid,))
            c.executemany("INSERT INTO client_aliases VALUES(?,?,?)",[(normalize(a),a,cid) for a in names])
            c.execute("INSERT OR IGNORE INTO clients(name,use_count,last_used_at) VALUES(?,0,?)",(name,now))
            if defaults is not None:
                c.execute("INSERT INTO client_preferences VALUES(?,?,?) ON CONFLICT(client_id,profile_id) "
                          "DO UPDATE SET data_json=excluded.data_json",(cid,pid,json.dumps(defaults,ensure_ascii=False)))
            result=_out(c,c.execute("SELECT * FROM client_records WHERE id=?",(cid,)).fetchone(),False)
        try:
            result["usage"]=usage_for(names)
        except (sqlite3.Error,OSError,ValueError) as exc:
            # The save committed; a failed statistics read must not report a
            # failed save and prompt the user to retry a completed mutation.
            result["usage"]={"total":0,"by_profile":{},"references":{},"errors":[str(exc)]}
        return {"ok":True,"record":result}
    except (ValueError,TypeError,sqlite3.Error) as exc:
        return {"ok":False,"error":str(exc)}

def set_archived(identity,archived=True):
    current=get_record(identity)
    if not current["ok"]:return current
    if not current.get("record"):return {"ok":False,"error":"Select a client."}
    return save_record({"id":current["record"]["id"],"archived":archived})

def remember(name):
    name=str(name or "").strip()
    if not name:return
    with _connection() as c:
        if not _alias(c,name):_insert(c,name)

# alias_key -> display name, rebuilt only when the shared database changed
# (2026-10-01). Same gate as `_prepare`: `db_version` moves on ANY commit, so a
# renamed client, a new alias or a merge is seen on the very next call, and a
# counter that cannot be read means "rebuild", never "serve the old one".
_canon_cache: dict[str, tuple[str, dict]] = {}


def canonical_map():
    with _connection() as c:
        path = _db_file(c)
        key = str(path)
        # Read the counter INSIDE the connection, after `_prepare` has synced:
        # the sync may itself have just added names.
        v = db_version.version(path) if path else None
        hit = _canon_cache.get(key)
        if v is not None and hit and hit[0] == v:
            return dict(hit[1])
        built = {r["alias_key"]:r["name"] for r in c.execute(
            "SELECT a.alias_key,r.name FROM client_aliases a JOIN client_records r ON r.id=a.client_id")}
    # The connection's exit commit can move the counter (if `_prepare` wrote),
    # so store the number as it stands now that the connection is closed.
    v_after = db_version.version(path) if path else None
    if v_after is not None and v_after == v:
        _canon_cache[key] = (v_after, built)
    else:
        _canon_cache.pop(key, None)
    return dict(built)

def aliases_for(name):
    with _connection() as c:
        row=_find(c,name)
        return _aliases(c,row) if row else [str(name or "").strip()]

def alias_keys_for(name):
    """The identity's aliases as normalized keys - what a query must match on.

    Callers used to compare saved document names with SQL `COLLATE NOCASE`,
    which is ASCII-only and whitespace-sensitive, so an alias resolved here but
    its documents did not come back. Match on these keys instead.
    """
    return list(dict.fromkeys(normalize(a) for a in aliases_for(name) if normalize(a)))

def canonical(name, identities=None):
    """Display name this spelling belongs to. Pass `identities` in a loop -
    `canonical_map()` opens a connection."""
    if identities is None:identities=canonical_map()
    return identities.get(normalize(name),str(name or "").strip())

def suggestions(prefix="",limit=100):
    with _connection() as c:
        rows=c.execute("SELECT r.id,r.name,a.alias FROM client_records r JOIN client_aliases a ON a.client_id=r.id "
                       "WHERE r.archived=0 ORDER BY r.name COLLATE NOCASE").fetchall()
        matches={}
        query=normalize(prefix)
        for r in rows:
            if query and query not in normalize(r["alias"]):continue
            matches[r["id"]]={"name":r["name"]}
        return list(matches.values())[:max(0,min(int(limit),500))]

# Characters a Windows path cannot hold. The document CODE goes into both the
# number and the folder name, so the same strip has to apply to both.
_CODE_UNSAFE = '\\/:*?"<>|'


def document_code(name):
    """The short code this client's documents are numbered and filed under.

    ONE implementation, called by `defaults_for` (so the editor chip shows it)
    and by `_sanitize_client` in all three numbering modules (so the number and
    the folder are built from it). A second copy of this rule is how the chip
    could say one thing while the number said another - and the chip exists to
    be trusted.

    A name that is not on file is used as typed: a new client must still be able
    to receive a document before anyone has filled in their record.
    """
    text = str(name or "").strip()
    try:
        text = canonical(text) or text
    except Exception:
        pass                      # never block numbering on the client store
    for ch in _CODE_UNSAFE:
        text = text.replace(ch, "")
    return text or "CLIENT"


def defaults_for(name):
    """Settings to apply when this client is picked on a document.

    `known` says whether the name IS one of our clients. The editors apply these
    automatically on selection, so an unknown name must fail SILENTLY - typing a
    new client is ordinary - while a real client whose settings cannot be
    applied (a receiving account missing from this profile) must be reported.
    """
    r=get_record(name)
    if not r["ok"]:return {**r,"known":False}
    if not r.get("record"):return {"ok":False,"error":"Select a client first.","known":False}
    record=r["record"]; defaults=record["defaults"]
    if record["archived"]:return {"ok":False,"known":False,
                                  "error":"This client is archived. Restore it before applying defaults."}
    key=defaults.get("bank_key","")
    if defaults.get("payment_method")=="cash":
        key=next((b["key"] for b in bank_options() if b["key"].casefold()=="cash"),"")
        if not key:return {"ok":False,"error":"Cash is unavailable in this company profile.","known":True}
    if defaults.get("payment_method")=="bank_transfer" and not key:
        return {"ok":False,"error":"Select a preferred receiving account in Clients Master Data.","known":True}
    if key and key not in {b["key"] for b in bank_options()}:
        return {"ok":False,"error":"The preferred receiving account is missing from this company profile.","known":True}
    address=defaults.get("address","")
    if not address and record["addresses"]:address=record["addresses"][0]["address"]
    # WHAT THE DOCUMENT PRINTS (Waleed's ruling, 2026-09-11): the legal name,
    # every time, falling back to the short name for a client who has none.
    # `name` stays the short code - it is what the number is built from and
    # what the app groups by, so a document printing the full legal name is
    # still filed, numbered and counted under TDC.
    print_name = str(record.get("legal_name") or "").strip() or record["name"]
    return {"ok":True,"known":True,**defaults,"name":record["name"],"aliases":record["aliases"],
            "address":address,"payment_bank_key":key,
            "print_name":print_name,"code":document_code(record["name"])}

def remove_unused(identity):
    current=get_record(identity)
    if not current["ok"]:return current
    if not current.get("record"):return {"ok":False,"error":"Select a client."}
    record=current["record"]; use=record["usage"]
    if use["references"] or use["errors"]:
        return {"ok":False,"error":"Client is referenced by saved data and cannot be removed.","usage":use}
    with _connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row=_find(c,record["id"])
        if not row:return {"ok":False,"error":"Client not found."}
        aliases=_aliases(c,row)
        # Recheck at mutation time; caller additionally owns the app/test lock.
        use=usage_for(aliases)
        if use["references"] or use["errors"]:return {"ok":False,"error":"Client is now referenced.","usage":use}
        alias_keys={normalize(a) for a in aliases}
        for legacy in c.execute("SELECT name FROM clients").fetchall():
            if normalize(legacy[0]) in alias_keys:
                c.execute("DELETE FROM clients WHERE name=?",(legacy[0],))
        c.execute("DELETE FROM client_preferences WHERE client_id=?",(row["id"],))
        c.execute("DELETE FROM client_aliases WHERE client_id=?",(row["id"],))
        c.execute("DELETE FROM client_records WHERE id=?",(row["id"],))
    return {"ok":True,"removed":record}

def merge_empty_records(target,sources):
    """Explicit maintenance operation: merge imported empty identities, not documents."""
    try:
        with _connection() as c:
            c.execute("BEGIN IMMEDIATE")
            dest=_find(c,target)
            if not dest:raise ValueError("Target client not found.")
            for source in sources:
                row=_find(c,source)
                if not row or row["id"]==dest["id"]:continue
                if json.loads(row["details_json"]) or c.execute(
                    "SELECT 1 FROM client_preferences WHERE client_id=?",(row["id"],)).fetchone():
                    raise ValueError(f'{row["name"]} has saved details. Review them before merging.')
                c.execute("UPDATE client_aliases SET client_id=? WHERE client_id=?",(dest["id"],row["id"]))
                c.execute("DELETE FROM client_records WHERE id=?",(row["id"],))
            c.execute("UPDATE client_records SET updated_at=? WHERE id=?",(_now(),dest["id"]))
        return get_record(dest["id"])
    except (ValueError,sqlite3.Error) as exc:return {"ok":False,"error":str(exc)}
