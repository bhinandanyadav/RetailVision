import getpass
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
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def main():
    load_dotenv()
    db_path = get_db_path()

    main_admin_name = "Admin"
    main_admin_email = "yadavabhinandan802@gmail.com"

    print("Create main admin user")
    print(f"Name: {main_admin_name}")
    print(f"Email: {main_admin_email}")

    raw_password = getpass.getpass("Main admin password (hidden): ")
    if not raw_password:
        print("Password required.")
        return

    password_hash = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = sqlite3.connect(db_path)
    try:
        ensure_users_table(conn)
        with conn:
            existing = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (main_admin_email.lower(),),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE users SET name = ?, role = ?, password_hash = ? WHERE email = ?",
                    (main_admin_name, "admin", password_hash, main_admin_email.lower()),
                )
            else:
                conn.execute(
                    "INSERT INTO users (name, email, role, password_hash) VALUES (?, ?, ?, ?)",
                    (main_admin_name, main_admin_email.lower(), "admin", password_hash),
                )

        print("Main admin user ready.")
        print("Create additional admin users (leave name blank to finish)")

        while True:
            name = input("Admin name: ").strip()
            if not name:
                break
            email = input("Admin email: ").strip().lower()
            if not email:
                print("Email required.")
                continue

            raw_password = getpass.getpass("Password (hidden): ")
            if not raw_password:
                print("Password required.")
                continue

            password_hash = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

            try:
                with conn:
                    conn.execute(
                        "INSERT INTO users (name, email, role, password_hash) VALUES (?, ?, ?, ?)",
                        (name, email, "admin", password_hash),
                    )
                print(f"Admin user created: {email}")
            except sqlite3.IntegrityError:
                print("That email already exists. Use the promote script instead.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
