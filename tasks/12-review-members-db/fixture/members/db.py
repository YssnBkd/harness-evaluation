"""Data access for the members area (SQLite)."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                                  created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS logins (user_id INTEGER NOT NULL REFERENCES users(id), at TEXT NOT NULL);
"""


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def add_user(conn, name, email):
    cur = conn.execute("INSERT INTO users (name, email) VALUES (?, ?)", (name, email))
    conn.commit()
    return cur.lastrowid


def get_user(conn, user_id):
    """The user as a dict, or None."""
    row = conn.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,)).fetchone()
    return None if row is None else {"id": row[0], "name": row[1], "email": row[2]}
