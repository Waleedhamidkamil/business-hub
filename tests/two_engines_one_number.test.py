"""Two engines, one company, one number each (multi-user, 2026-10-22).

The proof for Phase 1 of the office server. Two separate Python processes -
two "people" - save 40 new invoices each into ONE isolated copy of the data at
the same time, for each company. Every number must be different, all 80 saves
must succeed, and all 80 records must be in the database.

Before number_claims this failed: both processes read the same "newest + 1",
and the second save overwrote the first one's record.

Never touches the real data: everything lives under .test-work/two-engines.
"""
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".test-work" / "two-engines"
SAVES = 40

# One "person": point the app at the isolated data, then save SAVES invoices.
WORKER = r'''
import importlib, json, os, sys
from pathlib import Path
root, data, profile, saves = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
os.environ["BUSINESSHUB_USER"] = sys.argv[5]          # each engine is a different person
sys.path.insert(0, str(root))
from backend import profile_manager
from backend.profile_manager import ProfileManager
profile_manager._MANAGER = ProfileManager(data, root / "static")
from backend import config, database
importlib.reload(config); importlib.reload(database)
from backend.api import Api
api, out, retries = Api(), [], 0
for i in range(saves):
    payload = {"is_edit": False, "date": "22/10/2026", "inv_type": "materials",
               "client_name": "Two Engines Co", "items": [{"item": "Cement", "qty": 1}],
               "save_without_pdf": True}
    if i % 2 == 0:
        # what the editor does: show the next number, then save with it
        payload["inv_no"] = api.get_next_inv_number("Two Engines Co", "materials")["inv_no"]
    for attempt in range(20):
        r = api.save_inv(payload)
        if r.get("number_taken"):
            retries += 1      # the editor fetches the next number and saves again
            payload["inv_no"] = api.get_next_inv_number("Two Engines Co", "materials")["inv_no"]
            continue
        break
    out.append(r.get("inv_no") if r.get("ok") else "ERR:" + str(r.get("error")))
print("RETRIES", retries)
print("RESULT " + json.dumps(out))
'''

# One-time setup of the isolated data for a company: schema + the client.
SEED = r'''
import importlib, sys
from pathlib import Path
root, data, profile = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(root))
from backend import profile_manager
from backend.profile_manager import ProfileManager
m = ProfileManager(data, root / "static")
profile_manager._MANAGER = m
m.create_registry(profile)
s = m.settings_path(profile)
s.parent.mkdir(parents=True, exist_ok=True)
import json
s.write_text(json.dumps({"documents_root": str(data.parent / ("docs-" + profile))}), encoding="utf-8")
from backend import config, database, clients
importlib.reload(config); importlib.reload(database)
database.init_db()
res = clients.add_client("Two Engines Co")
assert res.get("ok"), res
'''


def run_profile(profile: str) -> None:
    data = WORK / profile / "data"
    if (WORK / profile).exists():
        shutil.rmtree(WORK / profile)
    data.mkdir(parents=True)
    seed = subprocess.run([sys.executable, "-c", SEED, str(ROOT), str(data), profile],
                          capture_output=True, text=True, timeout=300)
    assert seed.returncode == 0, seed.stderr[-2000:]

    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(ROOT), str(data), profile,
                               str(SAVES), person],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for person in ("waleed", "ahmed")]
    numbers: list[str] = []
    retries = 0
    for p in procs:
        out, err = p.communicate(timeout=900)
        assert p.returncode == 0, err[-2000:]
        retries += sum(int(ln.split()[1]) for ln in out.splitlines() if ln.startswith("RETRIES "))
        line = [ln for ln in out.splitlines() if ln.startswith("RESULT ")][-1]
        numbers += json.loads(line[len("RESULT "):])

    errors = [n for n in numbers if n.startswith("ERR:")]
    assert not errors, f"[{profile}] {len(errors)} of {len(numbers)} saves failed; first: {errors[0]}"
    distinct = set(numbers)
    assert len(distinct) == len(numbers), (
        f"[{profile}] {len(numbers)} saves got only {len(distinct)} distinct numbers - "
        f"two people were handed the same number")

    db = profile_manager_db(data, profile)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    finally:
        conn.close()
    assert rows == len(numbers), f"[{profile}] {len(numbers)} saves but {rows} invoices on file - saves overwrote each other"
    print(f"[{profile}] two engines: {len(numbers)} saves, {len(distinct)} distinct numbers, "
          f"{rows} on file, 0 errors, {retries} 'number taken' retries")


def profile_manager_db(data: Path, profile: str) -> Path:
    return data / "profiles" / profile / "po_tool.db"


def main() -> None:
    for profile in ("abraj", "eshraqat"):
        run_profile(profile)
    print("two engines one number: passed")


if __name__ == "__main__":
    main()
