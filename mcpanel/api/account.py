"""The signed-in user's own account: who am I, my signed-in devices, my password and recovery codes."""
import time

from flask import Blueprint, abort, g, request

from .. import accounts, auth, logs
from ..db import get_db
from .common import json_body

bp = Blueprint('account', __name__)


def current_token_hash():
    return accounts.hashed(request.cookies[auth.COOKIE])


def check_own_password(password):
    """Abort with a 403 unless password is the signed-in user's. Wrong tries count against the
    sign-in throttle, so a stolen session can't be used to guess the password quickly."""
    user = accounts.user_by_id(g.user['id'])
    error = auth.throttled(lambda: None if accounts.password_matches(user, str(password or '')) else 'That password is wrong')
    if error:
        abort(429 if error == auth.TOO_FAST else 403, error)


@bp.get('/me')
def me():
    with get_db() as conn:
        left = conn.execute('SELECT COUNT(*) FROM recovery_codes WHERE user_id = ?', (g.user['id'],)).fetchone()[0]
    return {'username': g.user['username'], 'role': g.user['role'], 'recovery_codes_left': left}


@bp.get('/account/sessions')
def list_sessions():
    """The devices signed in to this account, most recently seen first. An id is the start of the
    stored token hash: enough to name a session, useless for signing in."""
    current = current_token_hash()
    with get_db() as conn:
        rows = conn.execute('''SELECT token_hash, created, last_seen, ip, user_agent FROM sessions
                               WHERE user_id = ? AND expires > ? ORDER BY last_seen DESC''',
                            (g.user['id'], time.time())).fetchall()
    return {'sessions': [{'id': row['token_hash'][:16], 'created': row['created'], 'last_seen': row['last_seen'],
                          'ip': row['ip'], 'user_agent': row['user_agent'], 'current': row['token_hash'] == current}
                         for row in rows]}


@bp.delete('/account/sessions/<session_id>')
def revoke_session(session_id):
    """Sign one of this account's devices out."""
    with get_db() as conn:
        if not conn.execute('DELETE FROM sessions WHERE user_id = ? AND substr(token_hash, 1, 16) = ?',
                            (g.user['id'], session_id)).rowcount:
            abort(404, 'No such session')
        conn.commit()
    logs.audit('session_revoked', user_id=g.user['id'], username=g.user['username'], sessions=1)
    return {}


@bp.post('/account/sessions/sign-out-others')
def sign_out_others():
    """Sign every other device of this account out."""
    with get_db() as conn:
        ended = conn.execute('DELETE FROM sessions WHERE user_id = ? AND token_hash != ?',
                             (g.user['id'], current_token_hash())).rowcount
        conn.commit()
    logs.audit('session_revoked', user_id=g.user['id'], username=g.user['username'], sessions=ended)
    return {}


@bp.post('/account/password')
def change_password():
    """{current, new}. Every other device is signed out; this one stays signed in."""
    data = json_body()
    new = data.get('new')
    if not isinstance(new, str) or len(new) < accounts.MIN_PASSWORD:
        abort(400, f'Choose a new password of at least {accounts.MIN_PASSWORD} characters')
    check_own_password(data.get('current'))
    accounts.set_password(g.user['id'], new, request.cookies[auth.COOKIE])
    logs.audit('password_changed', user_id=g.user['id'], username=g.user['username'])
    return {}


@bp.post('/account/recovery-codes')
def new_recovery_codes():
    """{password} -> ten new recovery codes, shown only in this response. The old ones stop working."""
    check_own_password(json_body().get('password'))
    codes = accounts.new_recovery_codes(g.user['id'])
    logs.audit('recovery_codes_new', user_id=g.user['id'], username=g.user['username'])
    return {'codes': codes}
