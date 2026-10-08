"""SQLite access. The database holds only what Docker doesn't know: accounts, sessions, invites,
recovery codes, and which member may use which server. Times are Unix seconds."""
import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
        password_hash TEXT NOT NULL,              -- argon2id
        totp_secret TEXT NOT NULL,                -- base32
        totp_last_step INTEGER NOT NULL DEFAULT 0, -- the last 30-second step a code was accepted for
        failed_tries INTEGER NOT NULL DEFAULT 0,  -- wrong passwords or codes in a row
        locked_until REAL NOT NULL DEFAULT 0,
        password_changed REAL NOT NULL,
        created REAL NOT NULL
    );
    -- A signed-in device. The cookie holds a random token; only its SHA-256 is stored.
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires REAL NOT NULL,
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    -- Single-use recovery codes, stored as SHA-256 of the normalized code.
    CREATE TABLE IF NOT EXISTS recovery_codes (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL,
        PRIMARY KEY (user_id, code_hash)
    );
    -- A one-time invite link. token_hash is SHA-256 of the token in the link; servers is a JSON list
    -- of server ids; totp_secret is made with the invite, so every visit to the link shows the same QR.
    CREATE TABLE IF NOT EXISTS invites (
        token_hash TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
        servers TEXT NOT NULL DEFAULT '[]',
        totp_secret TEXT,
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        created REAL NOT NULL,
        expires REAL NOT NULL
    );
    -- Which servers a member may see and run. Admins may use every server and have no rows here.
    CREATE TABLE IF NOT EXISTS server_access (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        server TEXT NOT NULL,
        PRIMARY KEY (user_id, server)
    );
'''


@contextmanager
def get_db():
    """Open a short-lived connection. WAL and a busy timeout let request threads share the file."""
    conn = sqlite3.connect(config.DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create the schema if needed. Safe to run on every start."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
