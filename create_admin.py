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


def main():
    load_dotenv()
    db_path = get_db_path()

    name = "Admin"
    email = "yadavabhinandan802@gmail.com"

    print("Create admin user")
    print(f"Name: {name}")
    print(f"Email: {email}")

    raw_password = getpass.getpass("Password (hidden): ")
    if not raw_password:
        print("Password required.")
        return

    password_hash = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO users (name, email, role, password_hash) VALUES (?, ?, ?, ?)",
                (name, email, "admin", password_hash),
            )
    except sqlite3.IntegrityError:
        print("That email already exists. Use the promote script instead.")
    finally:
        conn.close()

    print("Admin user created.")


if __name__ == "__main__":
    main()
