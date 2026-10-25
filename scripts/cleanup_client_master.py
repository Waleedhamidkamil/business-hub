"""Audited client cleanup. Defaults to backup + rehearsal, never live mutation.

Run with --apply only while holding the agent-board test lock. All three live
databases are locked and compared to their backups before the client-only
transaction is published. Materials, documents and profile settings are never
copied back. Reports and SQLite recovery copies stay in data/client-backups/.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing, ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CLIENT_TABLES=('clients','client_records','client_aliases','client_preferences','client_master_meta')
REMOVE=(
    '__POMEM_TEST__ Client','__USEDIN__ Client','Autolink Test Client','C','Client',
    'Copy To Invoice Test Client','Cost Scope Client','Delete Test Client',
    'LPO Client A __LPOTEST__','PO Link Client','Re-sign Test Client',
    'Signed Copy Test Client','Signed Page Test Client __SIGNPAGE__','Source DN Test Client',
)
ARCHIVE=('77 Insaat','ACME','AUDCO','BETA','Chemie-Tech','Daeah','Daeshin','Daesun',
         'Demo','ILK Construction','JGC Corporation','LINKCO','PVWCO','RENTCO','TESTCO','X')
HDEC_ALIASES=('Hyundai Engineering & Construction',
              'Hyundai Engineering and Construction Co., Ltd',
              'Hyundai Engineering & construction ltd')

def qi(name):
    return '"'+name.replace('"','""')+'"'

def fingerprint(conn):
    """Order-independent, exact typed row + table-schema digests."""
    result={}
    for name,sql in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        rows=sorted(repr(tuple(r)) for r in conn.execute('SELECT * FROM '+qi(name)))
        digest=hashlib.sha256((str(sql)+'\n'+'\n'.join(rows)).encode('utf-8')).hexdigest()
        result[name]={'rows':len(rows),'sha256':digest}
    return result

def read_fingerprint(path):
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as c:
        return fingerprint(c)

def backup(source,destination):
    if not source.is_file():raise RuntimeError(f'Database unavailable: {source}')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:src.backup(dst)

def publish(live,clean,expected,profiles):
    """Lock all dependencies, fail on drift, commit only whitelisted client tables."""
    with ExitStack() as stack:
        for path,original in profiles.items():
            c=stack.enter_context(closing(sqlite3.connect(path.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)))
            c.execute('BEGIN IMMEDIATE')
            if fingerprint(c)!=original:raise RuntimeError(f'Profile data changed: {path}')
        c=stack.enter_context(closing(sqlite3.connect(live.resolve().as_uri()+'?mode=rw',uri=True,timeout=3)))
        c.execute('ATTACH DATABASE ? AS reviewed',(clean.resolve().as_uri()+'?mode=ro',))
        c.execute('BEGIN IMMEDIATE')
        try:
            if fingerprint(c)!=expected:raise RuntimeError('Live library changed since backup; rehearse again.')
            source_tables={r[0]:r[1] for r in c.execute("SELECT name,sql FROM reviewed.sqlite_master WHERE type='table'")}
            for table in CLIENT_TABLES:
                if table not in source_tables:continue
                if table not in expected:c.execute(source_tables[table])
                cols=','.join(qi(r[1]) for r in c.execute('PRAGMA reviewed.table_info('+qi(table)+')'))
                c.execute('DELETE FROM main.'+qi(table))
                c.execute(f'INSERT INTO main.{qi(table)} ({cols}) SELECT {cols} FROM reviewed.{qi(table)}')
            for (sql,) in c.execute("SELECT sql FROM reviewed.sqlite_master WHERE type='index' AND tbl_name='client_aliases' AND sql IS NOT NULL").fetchall():
                c.execute(sql.replace('CREATE INDEX ','CREATE INDEX IF NOT EXISTS ',1))
            after=fingerprint(c)
            if {k:v for k,v in after.items() if k not in CLIENT_TABLES}!={k:v for k,v in expected.items() if k not in CLIENT_TABLES}:
                raise RuntimeError('Protected data changed; cleanup rolled back.')
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Integrity check failed.')
            c.commit()
        except Exception:
            c.rollback()
            raise

def require(result):
    if not result.get('ok'):raise RuntimeError(str(result))
    return result

def rehearse(data_root,output):
    sys.path.insert(0,str(ROOT))
    from backend import profile_manager
    original=profile_manager._MANAGER
    fallback=os.environ.pop('DOCUMENTS_HUB_LEGACY_FALLBACK',None)
    pm=profile_manager.ProfileManager(output/'rehearsal/data',ROOT/'static')
    registry=data_root/'profile-registry.json'
    registry_bytes=registry.read_bytes()
    live_shared=data_root/'shared/library.db'
    shared_backup=output/'library.before.db'
    backup(live_shared,shared_backup)
    before=read_fingerprint(shared_backup)
    backup(shared_backup,pm.shared_db_path())
    pm.create_registry('abraj')
    profile_before={}
    settings_bytes={}
    for pid in pm.VALID_IDS:
        src=data_root/'profiles'/pid/'po_tool.db'
        saved=output/f'{pid}.before.db'
        backup(src,saved); profile_before[src]=read_fingerprint(saved)
        backup(saved,pm.profile_db_path(pid))
        settings=data_root/'profiles'/pid/'settings.json'
        settings_bytes[settings]=settings.read_bytes()
        shutil.copy2(settings,pm.settings_path(pid))
    profile_manager._MANAGER=pm
    report={'removed':[],'archived':[],'skipped':[], 'before':before,'backup':str(output),'applied':False}
    try:
        from backend import config,client_master as cm
        importlib.reload(config)
        cm.seed_once(config.QUOTE_CLIENTS_SEED)
        for name in REMOVE:
            r=cm.get_record(name)
            if not r['ok']:report['skipped'].append(name); continue
            record=r['record']
            if record['aliases'] or record['contacts'] or record['addresses'] or record['notes'] or record['defaults']:
                raise RuntimeError(f'Test-name record has details and needs review: {name}')
            report['removed'].append(require(cm.remove_unused(record['id']))['removed'])
        for name in ARCHIVE:
            r=cm.get_record(name)
            if not r['ok']:report['skipped'].append(name);continue
            if r['record']['usage']['errors'] or r['record']['usage']['references']:
                raise RuntimeError(f'Archive candidate gained references: {name}')
            require(cm.set_archived(r['record']['id'],True)); report['archived'].append(name)
        require(cm.merge_empty_records('HDEC',HDEC_ALIASES[:2]))
        hdec=require(cm.get_record('HDEC'))['record']
        require(cm.save_record({'id':hdec['id'],'aliases':list(dict.fromkeys(hdec['aliases']+list(HDEC_ALIASES)))}))
        tdc=require(cm.get_record('TDC'))['record']
        require(cm.save_record({'id':tdc['id'],'aliases':list(dict.fromkeys(tdc['aliases']+['CCCC ltd']))}))
        for pid in pm.VALID_IDS:
            pm.activate(pid);importlib.reload(config)
            banks={b['key'].casefold():b['key'] for b in cm.bank_options()}
            preferred=banks.get('bob') or banks.get('adib')
            if not preferred:raise RuntimeError(f'No confirmed HDEC receiving account in {pid}')
            # Do not overwrite any preferences added since the original audit.
            for name in ('HDEC','TDC'):
                if require(cm.get_record(name))['record']['defaults']:
                    raise RuntimeError(f'{pid}/{name} already has preferences; review before replacing.')
            require(cm.save_record({'id':hdec['id'],'defaults':{
                'payment_method':'bank_transfer','bank_key':preferred,
                'alternative_bank_keys':[key for key in (banks.get('adib'),banks.get('bob')) if key and key!=preferred]}}))
            if 'cash' not in banks:raise RuntimeError(f'Cash unavailable in {pid}')
            require(cm.save_record({'id':tdc['id'],'defaults':{'payment_method':'cash','bank_key':banks['cash']}}))
        after=read_fingerprint(pm.shared_db_path())
        assert {k:v for k,v in before.items() if k not in CLIENT_TABLES}=={k:v for k,v in after.items() if k not in CLIENT_TABLES}
        for pid in pm.VALID_IDS:
            src=data_root/'profiles'/pid/'po_tool.db'
            assert read_fingerprint(pm.profile_db_path(pid))==profile_before[src]
        report['after']=after
        report['active_clients']=[r['name'] for r in cm.list_records()['records']]
        report['protected_materials_unchanged']=True
        report['protected_documents_unchanged']=True
    finally:
        profile_manager._MANAGER=original
        if fallback is not None:os.environ['DOCUMENTS_HUB_LEGACY_FALLBACK']=fallback
        if 'backend.config' in sys.modules:importlib.reload(sys.modules['backend.config'])
    return report,pm.shared_db_path(),before,profile_before,{registry:registry_bytes,**settings_bytes}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    data_root=ROOT/'data'; output=data_root/'client-backups'/stamp
    output.mkdir(parents=True,exist_ok=False)
    report,clean,before,profiles,files=rehearse(data_root,output)
    if args.apply:
        if any(path.read_bytes()!=content for path,content in files.items()):
            raise RuntimeError('Company settings or active profile changed; rehearse again.')
        publish(data_root/'shared/library.db',clean,before,profiles)
        report['applied']=True
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('before','after','removed')},ensure_ascii=False,indent=2))
    print(f"Removed test records: {len(report['removed'])}")

if __name__=='__main__':main()
