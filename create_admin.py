import os
import sqlite3

import bcrypt
from dotenv import load_dotenv


def get_db_path():
    db_path = os.environ.get("STORE_TRACKER_DB_PATH", "store_tracker.sqlite")
    if os.path.isabs(db_path):
        return db_path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), db_path))


def ensure_users_table(conn):
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL DEFAULT 'viewer',
                password_hash TEXT NOT NULL,
                email_verified INTEGER NOT NULL DEFAULT 0,
                verify_token TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur = conn.execute("PRAGMA table_info(users)")
        existing_cols = {row[1] for row in cur.fetchall()}
        if 'email_verified' not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")
        if 'verify_token' not in existing_cols:
            conn.execute("ALTER TABLE users ADD COLUMN verify_token TEXT")


def main():
    load_dotenv()
    db_path = get_db_path()

    name = "Abhinandan"
    email = "yadavabhinandan802@gmail.com"
    password = "386551"
    role = "admin"

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = sqlite3.connect(db_path)
    try:
        ensure_users_table(conn)
        existing = conn.execute(
            "SELECT id FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE users SET name = ?, role = ?, password_hash = ?, email_verified = 1 WHERE email = ?",
                (name, role, password_hash, email.lower()),
            )
            print(f"Admin updated: {name} <{email.lower()}>")
        else:
            conn.execute(
                "INSERT INTO users (name, email, role, password_hash, email_verified) VALUES (?, ?, ?, ?, 1)",
                (name, email.lower(), role, password_hash),
            )
            print(f"Admin created: {name} <{email.lower()}>")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
