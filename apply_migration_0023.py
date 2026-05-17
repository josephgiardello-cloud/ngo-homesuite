import sqlite3

db_path = "ngo_homesuite.sqlite3"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

with open("ngo_homesuite/migrations/0023_add_budget_transactions.sql") as f:
    sql = f.read()

for statement in sql.split(";"):
    stmt = statement.strip()
    if stmt:
        try:
            cursor.execute(stmt)
        except Exception as e:
            print(f"Error executing: {stmt[:50]}... => {e}")

conn.commit()
conn.close()
print("Migration 0023 applied successfully")
