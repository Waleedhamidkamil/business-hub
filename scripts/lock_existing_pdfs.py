"""Lock every PDF the app has already made (2026-10-07).

New PDFs are locked as they are written (backend/pdf_lock.py). This does the
same to the ones made before: our documents in both companies, the clients'
returned signed copies, acknowledged LPOs, reports and the recycle bin. A
client's own LPO is theirs and is left alone, as are the app's internal files.

    python scripts/lock_existing_pdfs.py            dry run - says what it would do
    python scripts/lock_existing_pdfs.py --apply    backs up, locks, verifies

Safety, per file:
  * skipped if already encrypted, digitally (certificate) signed - rewriting
    one would break the signature - or unreadable;
  * the original is copied to the backup folder first (outside every folder
    the app scans, so it can never be listed as a document);
  * after locking, it must open with no password and have the same page count
    and the same text on every page, or the original is put back;
  * its modified/accessed dates are kept;
  * a file open in another program is reported and left as it was.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

ROOTS = [Path(r"D:\PO TOOL"), Path(r"D:\ESHRAQAT DOCUMENTS")]
DOC_FOLDERS = {"PURCHASE ORDERS", "QUOTATIONS", "DELIVERY NOTES", "INVOICES", "REPORTS", "RECYCLE"}
BACKUP = Path(r"D:\PDF backups") / f"before-lock-{datetime.now():%Y%m%d-%H%M%S}"


def candidates():
    for root in ROOTS:
        if not root.is_dir():
            continue
        for top in root.iterdir():
            if not top.is_dir():
                continue
            lpos = top.name == "LPOs"
            if top.name not in DOC_FOLDERS and not lpos:
                continue
            for p in top.rglob("*"):
                if p.suffix.lower() != ".pdf" or not p.is_file():
                    continue
                # A client's LPO is theirs; only the copy we stamped is ours.
                if lpos and not p.name.lower().endswith(".acknowledged.pdf"):
                    continue
                yield root, p


def why_skip(path: Path, reader) -> str | None:
    if reader.is_encrypted:
        return "already encrypted"
    try:
        root = reader.trailer["/Root"]
        form = root.get("/AcroForm")
        if form is not None and int(form.get_object().get("/SigFlags", 0) or 0) & 1:
            return "digitally signed"
    except Exception:
        pass
    return None


def page_texts(reader) -> list[str]:
    return [(pg.extract_text() or "") for pg in reader.pages]


def main(apply: bool) -> int:
    from pypdf import PdfReader
    from backend import pdf_lock

    if not pdf_lock.settings()["lock"]:
        print("Locking is switched off on this machine (secrets.json) - nothing to do.")
        return 1
    plan, skipped = [], []
    for root, p in candidates():
        try:
            r = PdfReader(str(p))
            reason = why_skip(p, r)
            n = len(r.pages)
        except Exception as e:
            reason, n = f"unreadable ({type(e).__name__})", 0
        (skipped if reason else plan).append((root, p, reason, n))

    by = {}
    for root, p, _, _ in plan:
        k = f"{root.name} / {p.relative_to(root).parts[0]}"
        by[k] = by.get(k, 0) + 1
    print(f"To lock: {len(plan)} PDFs, "
          f"{sum(p.stat().st_size for _, p, _, _ in plan) / 1048576:.0f} MB")
    for k in sorted(by):
        print(f"  {by[k]:5d}  {k}")
    print(f"Skipped: {len(skipped)}")
    for root, p, reason, _ in skipped:
        print(f"  {reason:22s} {p.relative_to(root)}")
    if not apply:
        print("\nDry run only. Run with --apply to back up and lock.")
        return 0

    log = {"started": datetime.now().isoformat(timespec="seconds"), "backup": str(BACKUP),
           "locked": [], "failed": [], "skipped": [[str(p), r] for _, p, r, _ in skipped]}
    t0 = time.time()
    for i, (root, p, _, _) in enumerate(plan, 1):
        dest = BACKUP / root.name / p.relative_to(root)
        st = p.stat()
        try:
            before = page_texts(PdfReader(str(p)))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            pdf_lock.lock(p)
            after_reader = PdfReader(str(p))
            ok = after_reader.is_encrypted and page_texts(after_reader) == before
            if not ok:
                shutil.copy2(dest, p)
                log["failed"].append([str(p), "content check failed - original restored"])
            else:
                os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
                log["locked"].append(str(p))
        except PermissionError:
            log["failed"].append([str(p), "open in another program - left as it was"])
        except Exception as e:
            try:
                if dest.exists():
                    shutil.copy2(dest, p)
                    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
            except OSError:
                pass
            log["failed"].append([str(p), f"{type(e).__name__}: {e} - original restored"])
        if i % 50 == 0:
            print(f"  {i}/{len(plan)} ...")
    log["seconds"] = round(time.time() - t0, 1)
    BACKUP.mkdir(parents=True, exist_ok=True)
    (BACKUP / "lock-log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"\nLocked {len(log['locked'])}, failed {len(log['failed'])}, "
          f"skipped {len(skipped)} in {log['seconds']}s. Originals: {BACKUP}")
    for f, why in log["failed"]:
        print(f"  FAILED {why}: {f}")
    return 0 if not log["failed"] else 2


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
