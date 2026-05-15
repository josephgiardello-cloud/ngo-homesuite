import getpass
import sqlite3
import sys
import os
from argon2 import PasswordHasher
from ngo_homesuite.db.schema import hash_pii

DB_PATH = os.getenv('DB_PATH', 'ngo_homesuite.sqlite3')
ph = PasswordHasher()

def create_initial_admin():
    print("=== Initial Admin Setup ===")
    username = input("Enter admin username: ").strip()
    name = input("Enter admin full name: ").strip()
    email = input("Enter admin email: ").strip()
    password = getpass.getpass("Enter admin password: ")
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.", file=sys.stderr)
        sys.exit(1)
    password_hash = ph.hash(password)
    email_hash = hash_pii(email)
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        # Check if any admin exists
        cur.execute("SELECT COUNT(*) FROM staff WHERE role = 'admin'")
        if cur.fetchone()[0] > 0:
            print("Admin already exists. Aborting setup.", file=sys.stderr)
            conn.close()
            sys.exit(1)
        cur.execute("INSERT INTO staff (username, password_hash, role, name, email) VALUES (?, ?, 'admin', ?, ?)",
                    (username, password_hash, name, email_hash))
        conn.commit()
        conn.close()
        print("Initial admin created successfully.")
    except Exception as e:
        print(f"Failed to create admin: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    create_initial_admin()
