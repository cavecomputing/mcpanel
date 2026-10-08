"""Users, for admins: list, add, change role and servers, reset sign-in, remove."""
import json

from flask import Blueprint, abort, g

from .. import accounts, auth, logs, servers
from ..db import get_db
from .common import json_body

bp = Blueprint('users', __name__)
bp.before_request(auth.require_admin)  # every route here is for admins

# Holds unless the user is the only admin left: mcpanel always keeps one.
NOT_LAST_ADMIN = "(role != 'admin' OR (SELECT COUNT(*) FROM users WHERE role = 'admin') > 1)"


def role_arg(role):
    if role not in accounts.ROLES:
        abort(400, 'The role is admin or member')
    return role


def servers_arg(server_ids):
    """The server ids a member may use, each checked. Missing means none."""
    if server_ids is None:
        return []
    if not isinstance(server_ids, list):
        abort(400, 'Expected a list of server ids')
    return sorted({servers.check_id(server_id) for server_id in server_ids})


def user_dicts():
    """Every user as the API shows them, by username. one_time_password_expires is when the one-time
    password of an account waiting for its first sign-in stops working, and null for everyone else."""
    with get_db() as conn:
        rows = conn.execute('''SELECT id, username, role, created,
                                      (SELECT MAX(last_seen) FROM sessions WHERE user_id = users.id) AS last_seen,
                                      (SELECT json_group_array(server) FROM server_access WHERE user_id = users.id) AS servers,
                                      CASE WHEN totp_secret IS NULL THEN password_changed + ? END AS one_time_password_expires
                               FROM users ORDER BY username''', (accounts.ONE_TIME_FOR,)).fetchall()
    return [{'id': row['id'], 'username': row['username'], 'role': row['role'], 'servers': sorted(json.loads(row['servers'])),
             'last_seen': row['last_seen'], 'created': row['created'],
             'one_time_password_expires': row['one_time_password_expires']} for row in rows]


def user_dict(user_id):
    """One user as the API shows them."""
    return next(user for user in user_dicts() if user['id'] == user_id)


@bp.get('/users')
def list_users():
    return {'users': user_dicts()}


@bp.post('/users')
def create_user():
    """Add an account: {username, role, servers} -> {user, one_time_password}. The one-time password,
    shown only here, signs them in once to choose their own and set up two-factor sign-in."""
    data = json_body()
    username, role = accounts.check_username(data.get('username')), role_arg(data.get('role'))
    server_ids = servers_arg(data.get('servers')) if role == 'member' else []
    user_id, password = accounts.create_user(username, role, server_ids)
    logs.audit('user_created', user_id=user_id, username=username, role=role, servers=server_ids)
    return {'user': user_dict(user_id), 'one_time_password': password}, 201


@bp.put('/users/<int:user_id>')
def change_user(user_id):
    """Set a user's role and, for a member, the servers they may use: {role, servers}."""
    data = json_body()
    role = role_arg(data.get('role'))
    server_ids = servers_arg(data.get('servers')) if role == 'member' else []
    with get_db() as conn:
        if conn.execute('SELECT 1 FROM users WHERE id = ?', (user_id,)).fetchone() is None:
            abort(404, 'No such user')
        if not conn.execute(f"UPDATE users SET role = :role WHERE id = :id AND (:role = 'admin' OR {NOT_LAST_ADMIN})",
                            {'role': role, 'id': user_id}).rowcount:
            abort(409, "That's the only admin; make someone else an admin first")
        conn.execute('DELETE FROM server_access WHERE user_id = ?', (user_id,))
        conn.executemany('INSERT INTO server_access (user_id, server) VALUES (?, ?)',
                         [(user_id, server_id) for server_id in server_ids])
        conn.commit()
    user = user_dict(user_id)
    logs.audit('user_changed', user_id=user_id, username=user['username'], role=role, servers=server_ids)
    return user


@bp.post('/users/<int:user_id>/reset')
def reset_user(user_id):
    """Start someone else's sign-in over, for a lost phone and lost recovery codes -> {one_time_password}.
    Their password, TOTP and recovery codes stop working, they are signed out everywhere, and a lock
    is lifted. The new one-time password is shown only here."""
    if user_id == g.user['id']:
        abort(400, "You can't reset your own sign-in")
    user = accounts.user_by_id(user_id)
    if user is None:
        abort(404, 'No such user')
    password = accounts.reset_sign_in(user_id)
    logs.audit('sign_in_reset', user_id=user_id, username=user['username'])
    return {'one_time_password': password}


@bp.delete('/users/<int:user_id>')
def remove_user(user_id):
    """Remove a user, which signs them out everywhere at once. Not yourself, and never the last admin."""
    if user_id == g.user['id']:
        abort(400, "You can't remove yourself")
    with get_db() as conn:
        user = conn.execute('SELECT username FROM users WHERE id = ?', (user_id,)).fetchone()
        if user is None:
            abort(404, 'No such user')
        if not conn.execute(f'DELETE FROM users WHERE id = ? AND {NOT_LAST_ADMIN}', (user_id,)).rowcount:
            abort(409, "That's the only admin; make someone else an admin first")
        conn.commit()
    logs.audit('user_removed', user_id=user_id, username=user['username'])
    return {}
