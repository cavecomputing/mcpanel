"""SQLite access. The database holds only what Docker doesn't know: accounts, sessions, recovery
codes, which member may use which server, and scheduled restarts and backups. Times are Unix seconds."""
import sqlite3
from contextlib import contextmanager

from . import config

USERS_TABLE = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK (role IN ('platform_admin', 'admin', 'member')),
        password_hash TEXT NOT NULL,              -- argon2id
        -- base32. NULL until the first sign-in sets it up: the password is then a one-time one an admin
        -- handed out, which works for 24 hours from password_changed. '' when that sign-in chose a
        -- password but no TOTP, with MCPANEL_MFA_ENFORCEMENT off: the next sign-in with it on sets TOTP up.
        totp_secret TEXT,
        totp_last_step INTEGER NOT NULL DEFAULT 0, -- the last 30-second step a code was accepted for
        failed_tries INTEGER NOT NULL DEFAULT 0,  -- wrong passwords or codes in a row
        locked_until REAL NOT NULL DEFAULT 0,
        password_changed REAL NOT NULL,
        created REAL NOT NULL
    );
'''
SCHEMA = USERS_TABLE + '''
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
    -- Which servers a member may see and run. Admins and platform admins may use every server and have no rows here.
    CREATE TABLE IF NOT EXISTS server_access (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        server TEXT NOT NULL,
        PRIMARY KEY (user_id, server)
    );
    -- A server's restarts or backups on a schedule (schedules.py): a cron line, read in the IANA time
    -- zone of whoever set it.
    -- An id is never used twice, since a backup's name carries the id of the schedule that made it.
    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server TEXT NOT NULL,
        action TEXT NOT NULL CHECK (action IN ('restart', 'backup')),
        cron TEXT NOT NULL,
        timezone TEXT NOT NULL,
        keep INTEGER,  -- how many of its own backups a backup schedule keeps; NULL for a restart
        created REAL NOT NULL
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
    """Create the schema if needed, and bring a database from before the platform admin role up to
    it. Safe to run on every start."""
    with get_db() as conn:
        conn.executescript(SCHEMA)
        users_table = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()['sql']
        if 'platform_admin' not in users_table:
            # SQLite can't change a CHECK, so the table is made again with the new one, rows kept.
            # Foreign keys are off meanwhile, so dropping the old table doesn't cascade to sessions.
            conn.executescript(f'''PRAGMA foreign_keys=OFF;
                                   BEGIN;
                                   {USERS_TABLE.replace(' users (', ' users_new (')}
                                   INSERT INTO users_new SELECT * FROM users;
                                   DROP TABLE users;
                                   ALTER TABLE users_new RENAME TO users;
                                   COMMIT;
                                   PRAGMA foreign_keys=ON;''')
        # There is always a platform admin: the earliest admin becomes one when none is, which
        # upgrades an older panel, and one whose first account create-user made a plain admin.
        conn.execute('''UPDATE users SET role = 'platform_admin'
                        WHERE id = (SELECT id FROM users WHERE role = 'admin' ORDER BY created, id LIMIT 1)
                          AND NOT EXISTS (SELECT 1 FROM users WHERE role = 'platform_admin')''')
        conn.commit()
