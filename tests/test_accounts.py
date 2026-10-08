import json
import re
import time

import pyotp
import pytest
from argon2 import PasswordHasher
from werkzeug.exceptions import HTTPException

from mcpanel import accounts, create_app
from mcpanel.db import get_db

from .conftest import PASSWORD, make_user


@pytest.fixture
def clock(monkeypatch):
    """time.time() stands still at clock['now'] until a test moves it."""
    clock = {'now': time.time()}
    monkeypatch.setattr(time, 'time', lambda: clock['now'])
    return clock


def user_row(user_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()


@pytest.mark.parametrize('typed, expected', [('Matt', 'matt'), (' sam.o_k-1 ', 'sam.o_k-1'), ('a' * 32, 'a' * 32)])
def test_usernames_are_stored_lowercase(typed, expected):
    assert accounts.check_username(typed) == expected


@pytest.mark.parametrize('typed', ['', 'a' * 33, 'matt haney', 'mätt', 'matt/../x', None])
def test_bad_usernames_are_refused(typed):
    with pytest.raises(HTTPException) as e:
        accounts.check_username(typed)
    assert e.value.code == 400


def test_passwords_are_argon2id_hashes(app):
    user = make_user('matt')
    row = user_row(user['id'])
    assert row['password_hash'].startswith('$argon2id$')
    assert PASSWORD not in row['password_hash']
    assert accounts.password_matches(row, PASSWORD)
    assert not accounts.password_matches(row, PASSWORD + '!')
    assert not accounts.password_matches(None, PASSWORD)
    assert accounts.user_named(' MATT ')['id'] == user['id']


def test_an_outdated_hash_is_upgraded_at_sign_in(app):
    user = make_user('matt')
    weak = PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1).hash(PASSWORD)
    with get_db() as conn:
        conn.execute('UPDATE users SET password_hash = ? WHERE id = ?', (weak, user['id']))
        conn.commit()
    assert accounts.password_matches(user_row(user['id']), PASSWORD)
    upgraded = user_row(user['id'])['password_hash']
    assert upgraded != weak and not accounts.hasher.check_needs_rehash(upgraded)


def test_totp_accepts_one_step_either_side(clock):
    secret = pyotp.random_base32()
    totp, now = pyotp.TOTP(secret), clock['now']
    step = int(now) // 30
    assert accounts.totp_step(secret, totp.at(now), 0) == step
    assert accounts.totp_step(secret, totp.at(now - 30), 0) == step - 1
    assert accounts.totp_step(secret, totp.at(now + 30), 0) == step + 1
    assert accounts.totp_step(secret, totp.at(now - 60), 0) is None
    assert accounts.totp_step(secret, totp.at(now + 60), 0) is None
    code = totp.at(now)
    assert accounts.totp_step(secret, f' {code[:3]} {code[3:]} ', 0) == step
    assert accounts.totp_step(secret, totp.at(now), step) is None  # not newer than the last one used


def test_a_totp_code_works_once(app, clock):
    user = make_user('matt')
    code = pyotp.TOTP(user['totp_secret']).at(clock['now'])
    assert accounts.code_matches(user_row(user['id']), code)
    assert user_row(user['id'])['totp_last_step'] == int(clock['now']) // 30
    assert not accounts.code_matches(user_row(user['id']), code)


def test_recovery_codes(app):
    user = make_user('matt')
    codes = accounts.new_recovery_codes(user['id'])
    assert len(set(codes)) == 10
    assert all(re.fullmatch(r'[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}', code) for code in codes)
    with get_db() as conn:
        stored = {row[0] for row in conn.execute('SELECT code_hash FROM recovery_codes WHERE user_id = ?', (user['id'],))}
    assert not stored & set(codes)
    assert accounts.use_recovery_code(user['id'], ' ' + codes[0].upper().replace('-', ' ') + ' ')
    assert not accounts.use_recovery_code(user['id'], codes[0])  # single use
    other = make_user('sam')
    assert not accounts.use_recovery_code(other['id'], codes[1])  # only the owner's
    fresh = accounts.new_recovery_codes(user['id'])
    assert not accounts.use_recovery_code(user['id'], codes[1])  # new codes replace the old
    assert accounts.use_recovery_code(user['id'], fresh[0].replace('-', ''))


def test_sessions(app, clock):
    user = make_user('matt')
    token = accounts.new_session(user['id'], remember=False, ip='203.0.113.5', user_agent='x' * 500)
    assert accounts.session_user(token) == {'id': user['id'], 'username': 'matt', 'role': 'member'}
    assert accounts.session_user('not a token') is None and accounts.session_user(None) is None
    with get_db() as conn:
        row = conn.execute('SELECT * FROM sessions').fetchone()
    assert row['token_hash'] == accounts.hashed(token) != token
    assert row['ip'] == '203.0.113.5' and len(row['user_agent']) == 200
    assert row['expires'] - row['created'] == accounts.SESSION_FOR

    remembered = accounts.new_session(user['id'], remember=True)
    clock['now'] += accounts.SESSION_FOR + 1
    assert accounts.session_user(token) is None  # expired
    assert accounts.session_user(remembered)
    accounts.new_session(user['id'], remember=False)  # a sign-in prunes expired sessions
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 2
    accounts.end_session(remembered)
    assert accounts.session_user(remembered) is None


def test_last_seen_is_written_at_most_once_a_minute(app, clock):
    user = make_user('matt')
    token = accounts.new_session(user['id'], remember=True)
    started = clock['now']

    def last_seen():
        with get_db() as conn:
            return conn.execute('SELECT last_seen FROM sessions').fetchone()[0]
    clock['now'] += 30
    accounts.session_user(token)
    assert last_seen() == started
    clock['now'] += 31
    accounts.session_user(token)
    assert last_seen() == clock['now']


def test_set_password_keeps_only_the_current_session(app):
    user = make_user('matt')
    keep, other = (accounts.new_session(user['id'], remember=True) for _ in range(2))
    accounts.set_password(user['id'], 'a brand new password', keep)
    assert accounts.session_user(keep) and accounts.session_user(other) is None
    assert accounts.password_matches(user_row(user['id']), 'a brand new password')


def test_deleting_a_user_cascades(app):
    user = make_user('matt')
    token = accounts.new_session(user['id'], remember=True)
    accounts.new_recovery_codes(user['id'])
    with get_db() as conn:
        conn.execute("INSERT INTO server_access (user_id, server) VALUES (?, 'smp')", (user['id'],))
        conn.execute('DELETE FROM users WHERE id = ?', (user['id'],))
        conn.commit()
        assert all(conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
                   for table in ('sessions', 'recovery_codes', 'server_access'))
    assert accounts.session_user(token) is None


def test_invites(app, clock):
    token = accounts.create_invite('sam', 'member', ['creative', 'smp'], None)
    invite = accounts.invite_for(token)
    assert invite['username'] == 'sam' and json.loads(invite['servers']) == ['creative', 'smp']
    assert invite['token_hash'] == accounts.hashed(token) and invite['totp_secret']
    assert accounts.invite_for('nope') is None

    newer = accounts.create_invite('sam', 'member', [], None)  # replaces the first
    assert accounts.invite_for(token) is None and accounts.invite_for(newer)

    clock['now'] += accounts.INVITE_FOR + 1
    assert accounts.invite_for(newer) is None


def test_accepting_an_invite_makes_the_account_once(app):
    token = accounts.create_invite('sam', 'member', ['smp'], None)
    invite = accounts.invite_for(token)
    user_id = accounts.accept_invite(invite, PASSWORD, 1234)
    row = user_row(user_id)
    assert (row['username'], row['role'], row['totp_secret'], row['totp_last_step']) == \
        ('sam', 'member', invite['totp_secret'], 1234)
    assert accounts.servers_for(row) == {'smp'}
    assert accounts.invite_for(token) is None
    assert accounts.accept_invite(invite, PASSWORD, 1234) is None


def test_inviting_an_existing_username_is_a_conflict(app):
    make_user('sam')
    with pytest.raises(HTTPException) as e:
        accounts.create_invite('sam', 'member', [], None)
    assert e.value.code == 409


def test_who_may_use_which_server(app):
    admin, member = make_user('matt', 'admin'), make_user('sam')
    with get_db() as conn:
        conn.execute("INSERT INTO server_access (user_id, server) VALUES (?, 'smp')", (member['id'],))
        conn.commit()
    assert accounts.servers_for(admin) is None
    assert accounts.may_use(admin, 'anything')
    assert accounts.servers_for(member) == {'smp'}
    assert accounts.may_use(member, 'smp') and not accounts.may_use(member, 'creative')


def test_the_cookie_signing_key_is_never_stored(app):
    """With it, a copy of the database could sign a sign-in halfway through and skip the password."""
    assert len(app.secret_key) == 32 and create_app().secret_key != app.secret_key
    with get_db() as conn:
        assert app.secret_key.hex() not in '\n'.join(conn.iterdump()).lower()
