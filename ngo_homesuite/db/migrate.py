import sqlite3
import hashlib
import sys
from pathlib import Path

def auto_migrate(db_path=None):
    try:
        from ngo_homesuite.migrations import MIGRATIONS_DIR
    except ImportError:
        MIGRATIONS_DIR = Path(__file__).parent.parent / 'migrations'
    conn = sqlite3.connect(db_path or 'ngo_data.db', detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    try:
        try:
            cur = conn.execute('SELECT version, hash FROM schema_version ORDER BY version')
            applied = {row['version']: row['hash'] for row in cur.fetchall()}
        except sqlite3.OperationalError:
            applied = {}
        migration_files = sorted(MIGRATIONS_DIR.glob('*.sql'))
        for mf in migration_files:
            version = int(mf.name.split('_')[0])
            with open(mf, 'rb') as f:
                hash_val = hashlib.sha256(f.read()).hexdigest()
            if version in applied:
                if applied[version] != hash_val:
                    print(f"ERROR: Migration {mf.name} hash mismatch! DB: {applied[version]} File: {hash_val}")
                    sys.exit(1)
                continue
            sql = mf.read_text(encoding='utf-8')
            conn.executescript(sql)
            now_utc = __import__('datetime').datetime.utcnow().isoformat() + 'Z'
            conn.execute('INSERT INTO schema_version (version, applied_at_utc, hash) VALUES (?, ?, ?)',
                         (version, now_utc, hash_val))
            # Also update schema_hash table for versioned migrations
            try:
                conn.execute('INSERT INTO schema_hash (version, hash, applied_at_utc) VALUES (?, ?, ?)',
                             (version, hash_val, now_utc))
            except Exception:
                pass  # Table may not exist in early migrations
            conn.commit()
            print(f"Applied migration {version} ({mf.name}) with hash {hash_val}")
        print("All migrations applied and verified.")
    finally:
        conn.close()
