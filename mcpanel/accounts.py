"""Users, sessions, invites and recovery codes in the database, and who may use which server.

Passwords are argon2id hashes. Session tokens, invite tokens and recovery codes are random and
stored only as their SHA-256, so a copy of the database signs nobody in.
"""
import hashlib
import hmac
import json
import re
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import abort

from .db import get_db

ROLES = ('admin', 'member')
USERNAME = re.compile(r'[a-z0-9._-]{1,32}')
MIN_PASSWORD = 12
REMEMBER_FOR = 30 * 24 * 3600  # "Keep this device signed in"
SESSION_FOR = 24 * 3600  # otherwise the cookie ends with the browser, and the session after a day at most
INVITE_FOR = 24 * 3600
RECOVERY_CODES = 10

hasher = PasswordHasher()  # argon2id
# Checked when nobody has the username typed, so a wrong name takes as long as a wrong password.
DUMMY_HASH = hasher.hash(secrets.token_hex(16))


def hashed(text):
    """The SHA-256 hex digest under which a token or recovery code is stored."""
    return hashlib.sha256(text.encode()).hexdigest()


def check_username(username):
    """username in lowercase, or a 400 unless it is 1-32 of a-z, 0-9, dot, underscore and dash."""
    username = str(username or '').strip().lower()
    if not USERNAME.fullmatch(username):
        abort(400, 'A username is 1-32 characters: a-z, 0-9, dot, underscore and dash')
    return username


def create_user(username, role, password, totp_secret=None):
    """Add a user and return their id. A new TOTP secret is made unless one is given."""
    now = time.time()
    with get_db() as conn:
        user_id = conn.execute('''INSERT INTO users (username, role, password_hash, totp_secret, password_changed, created)
                                  VALUES (?, ?, ?, ?, ?, ?)''',
                               (username, role, hasher.hash(password), totp_secret or pyotp.random_base32(), now, now)).lastrowid
        conn.commit()
    return user_id


def user_named(username):
    """The users row for a username as typed (any case), or None."""
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE username = ?', (username.strip().lower(),)).fetchone()


def user_by_id(user_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


def password_matches(user, password):
    """Whether password is user's. None checks a dummy hash and is never a match. Upgrades an outdated hash."""
    try:
        hasher.verify(user['password_hash'] if user else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    if user is None:
        return False
    if hasher.check_needs_rehash(user['password_hash']):
        with get_db() as conn:
            conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (hasher.hash(password), user['id']))
            conn.commit()
    return True


def set_password(user_id, password, keep_token):
    """Change a user's password and end every session of theirs but the one whose token is keep_token."""
    with get_db() as conn:
        conn.execute('UPDATE users SET password_hash = ?, password_changed = ? WHERE id = ?',
                     (hasher.hash(password), time.time(), user_id))
        conn.execute('DELETE FROM sessions WHERE user_id = ? AND token_hash != ?', (user_id, hashed(keep_token)))
        conn.commit()


def totp_step(secret, code, last_step):
    """The 30-second step a 6-digit code is right for, one step either side of now, or None.

    Only steps after last_step count, so a code can't be used twice.
    """
    code = ''.join(code.split())
    now = int(time.time()) // 30
    totp = pyotp.TOTP(secret)
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def code_matches(user, code):
    """Whether code is user's current TOTP code and newer than the last one they used; records its step."""
    step = totp_step(user['totp_secret'], code, user['totp_last_step'])
    if step is None:
        return False
    with get_db() as conn:
        used = conn.execute('UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_last_step < ?',
                            (step, user['id'], step)).rowcount
        conn.commit()
    return bool(used)


def recovery_hash(code):
    """A recovery code's stored form: case, spaces and dashes don't matter."""
    return hashed(''.join(code.split()).replace('-', '').lower())


def new_recovery_codes(user_id):
    """Ten fresh single-use codes like 3f9a-04c1-e2b7, replacing the user's old ones. Shown once."""
    codes = ['-'.join(secrets.token_hex(2) for _ in range(3)) for _ in range(RECOVERY_CODES)]
    with get_db() as conn:
        conn.execute('DELETE FROM recovery_codes WHERE user_id = ?', (user_id,))
        conn.executemany('INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)',
                         [(user_id, recovery_hash(code)) for code in codes])
        conn.commit()
    return codes


def use_recovery_code(user_id, code):
    """Whether code is one of the user's unused recovery codes. A right one is used up."""
    with get_db() as conn:
        used = conn.execute('DELETE FROM recovery_codes WHERE user_id = ? AND code_hash = ?',
                            (user_id, recovery_hash(code))).rowcount
        conn.commit()
    return bool(used)


def new_session(user_id, remember, ip='', user_agent=''):
    """Sign a device in: the raw token for its cookie. Prunes expired sessions while at it."""
    token = secrets.token_urlsafe(32)
    now = time.time()
    with get_db() as conn:
        conn.execute('DELETE FROM sessions WHERE expires <= ?', (now,))
        conn.execute('''INSERT INTO sessions (token_hash, user_id, created, last_seen, expires, ip, user_agent)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (hashed(token), user_id, now, now, now + (REMEMBER_FOR if remember else SESSION_FOR),
                      ip or '', (user_agent or '')[:200]))
        conn.commit()
    return token


def session_user(token):
    """The user a session cookie's token signs in, as {'id', 'username', 'role'}, or None."""
    if not token:
        return None
    now = time.time()
    with get_db() as conn:
        row = conn.execute('''SELECT users.id, username, role, last_seen FROM sessions JOIN users ON users.id = user_id
                              WHERE token_hash = ? AND expires > ?''', (hashed(token), now)).fetchone()
        if row is None:
            return None
        if now - row['last_seen'] >= 60:  # a write a minute is plenty for "last seen"
            conn.execute('UPDATE sessions SET last_seen = ? WHERE token_hash = ?', (now, hashed(token)))
            conn.commit()
    return {'id': row['id'], 'username': row['username'], 'role': row['role']}


def end_session(token):
    with get_db() as conn:
        conn.execute('DELETE FROM sessions WHERE token_hash = ?', (hashed(token),))
        conn.commit()


def create_invite(username, role, server_ids, created_by):
    """A one-time invite token for a new account, valid for 24 hours, for a username that passed
    check_username(). Replaces an earlier invite for the same username. The account's TOTP secret
    is made now, so the set-up page always shows the same QR code."""
    now = time.time()
    with get_db() as conn:
        if conn.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
            abort(409, f'There is already a user called {username}')
        token = secrets.token_urlsafe(32)
        conn.execute('DELETE FROM invites WHERE username = ?', (username,))
        conn.execute('''INSERT INTO invites (token_hash, username, role, servers, totp_secret, created_by, created, expires)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (hashed(token), username, role, json.dumps(server_ids),
                      pyotp.random_base32(), created_by, now, now + INVITE_FOR))
        conn.commit()
    return token


def invite_for(token):
    """The unused, unexpired invite with this token, or None."""
    with get_db() as conn:
        return conn.execute('SELECT * FROM invites WHERE token_hash = ? AND expires > ?',
                            (hashed(token), time.time())).fetchone()


def accept_invite(invite, password, totp_step):
    """Turn an invite into its account, which may use the invite's servers; the new user's id.

    None when the invite was used meanwhile: claiming it first is what makes it single use.
    """
    with get_db() as conn:
        claimed = conn.execute('DELETE FROM invites WHERE token_hash = ?', (invite['token_hash'],)).rowcount
        conn.commit()
    if not claimed:
        return None
    user_id = create_user(invite['username'], invite['role'], password, invite['totp_secret'])
    with get_db() as conn:
        conn.execute('UPDATE users SET totp_last_step = ? WHERE id = ?', (totp_step, user_id))
        conn.executemany('INSERT INTO server_access (user_id, server) VALUES (?, ?)',
                         [(user_id, server_id) for server_id in json.loads(invite['servers'])])
        conn.commit()
    return user_id


def servers_for(user):
    """The ids of the servers user may see and run, or None for an admin, who may use them all."""
    if user['role'] == 'admin':
        return None
    with get_db() as conn:
        return {row['server'] for row in conn.execute('SELECT server FROM server_access WHERE user_id = ?', (user['id'],))}


def may_use(user, server_id):
    allowed = servers_for(user)
    return allowed is None or server_id in allowed
