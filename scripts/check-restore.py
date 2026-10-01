"""Demo-only backup/restore verification in a newly created isolated scratch database."""
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql

from app.settings import ROOT, Settings

settings = Settings()
if settings.mode != 'demo' or settings.database_url.rsplit('/', 1)[-1] != 'ai_leads_demo':
    raise SystemExit('This automatic check is only authorized for the local synthetic demo database')
bin_dir = ROOT / '.local/postgresql-17.11-4/pgsql/bin'
backup_dir = Path(os.environ.get('LOCALAPPDATA', str(ROOT / '.local'))) / 'AILeadAutomationPro/backups'
backup_dir.mkdir(parents=True, exist_ok=True)
backup = backup_dir / f'demo-{uuid4().hex[:10]}.dump'
scratch = f'ai_leads_restore_test_{uuid4().hex[:12]}'
admin = 'host=127.0.0.1 port=15432 user=ai_leads_admin dbname=postgres'
subprocess.run([str(bin_dir / 'pg_dump.exe'), '-h', '127.0.0.1', '-p', '15432',
                '-U', 'ai_leads_demo', '-d', 'ai_leads_demo', '-Fc', '-f', str(backup)], check=True)
created = False
try:
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(scratch)))
        created = True
    subprocess.run([str(bin_dir / 'pg_restore.exe'), '-h', '127.0.0.1', '-p', '15432',
                    '-U', 'ai_leads_admin', '-d', scratch, '--no-owner', '--exit-on-error', str(backup)], check=True)
    with psycopg.connect(f'host=127.0.0.1 port=15432 user=ai_leads_admin dbname={scratch}') as connection:
        schema = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        leads = connection.execute('SELECT count(*) FROM leads').fetchone()[0]
        bad = connection.execute('SELECT count(*) FROM jobs j LEFT JOIN leads l ON l.id=j.lead_id WHERE j.lead_id IS NOT NULL AND l.id IS NULL').fetchone()[0]
        assert bad == 0
        print(f'Restore VERIFIED: migration={schema}; leads={leads}; dangling job references=0')
    print(f'Synthetic backup retained: {backup}')
finally:
    if created and scratch.startswith('ai_leads_restore_test_'):
        # Only the database created by this invocation is removed, never the source.
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(scratch)))
