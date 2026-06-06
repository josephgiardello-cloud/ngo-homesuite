import sqlite3
conn = sqlite3.connect('data/homesuite.db')
cols = [r[1] for r in conn.execute('PRAGMA table_info(donations)').fetchall()]
print('HAS_campaign_id=', 'campaign_id' in cols)
print('HAS_version_id=', 'version_id' in cols)
print('HAS_created_at=', 'created_at' in cols)
print('HAS_updated_at=', 'updated_at' in cols)
print('COLS=', cols)
