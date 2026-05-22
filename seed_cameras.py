import os
import sqlite3

from dotenv import load_dotenv


def get_db_path():
    db_path = os.environ.get("STORE_TRACKER_DB_PATH", "store_tracker.sqlite")
    if os.path.isabs(db_path):
        return db_path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), db_path))


def ensure_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS camera_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source TEXT NOT NULL UNIQUE,
            default_mode TEXT NOT NULL DEFAULT 'tracking',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def seed_cameras(conn, cameras):
    for camera in cameras:
        conn.execute(
            """
            INSERT OR IGNORE INTO camera_sources (name, source, default_mode, enabled)
            VALUES (?, ?, ?, ?)
            """,
            (
                camera["name"],
                camera["source"],
                camera.get("default_mode", "tracking"),
                1 if camera.get("enabled", True) else 0,
            ),
        )


def main():
    load_dotenv()
    db_path = get_db_path()

    cameras = [
        {"name": "Front Camera", "source": "0", "default_mode": "tracking", "enabled": True},
        {"name": "Entrance Camera", "source": "1", "default_mode": "tracking", "enabled": True},
        {"name": "Aisle Camera", "source": "2", "default_mode": "heatmap", "enabled": False},
    ]

    conn = sqlite3.connect(db_path)
    try:
        ensure_tables(conn)
        with conn:
            seed_cameras(conn, cameras)
    finally:
        conn.close()

    print("Camera sources seeded.")


if __name__ == "__main__":
    main()
