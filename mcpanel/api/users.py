"""Users and invites, for admins: list, invite, change role and servers, remove."""
import json
import time

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
    """Every user as the API shows them, by username."""
    with get_db() as conn:
        rows = conn.execute('''SELECT id, username, role, created,
                                      (SELECT MAX(last_seen) FROM sessions WHERE user_id = users.id) AS last_seen,
                                      (SELECT json_group_array(server) FROM server_access WHERE user_id = users.id) AS servers
                               FROM users ORDER BY username''').fetchall()
    return [{'id': row['id'], 'username': row['username'], 'role': row['role'], 'servers': sorted(json.loads(row['servers'])),
             'last_seen': row['last_seen'], 'created': row['created']} for row in rows]


@bp.get('/users')
def list_users():
    """Every user, and the invites not yet used or expired."""
    with get_db() as conn:
        invites = conn.execute('SELECT username, role, servers, expires FROM invites WHERE expires > ? ORDER BY username',
                               (time.time(),)).fetchall()
    return {'users': user_dicts(),
            'invites': [{'username': row['username'], 'role': row['role'], 'servers': json.loads(row['servers']),
                         'expires': row['expires']} for row in invites]}


@bp.post('/users/invite')
def invite():
    """A one-time link for a new account: {username, role, servers}. The link is shown only here."""
    data = json_body()
    username, role = accounts.check_username(data.get('username')), role_arg(data.get('role'))
    server_ids = servers_arg(data.get('servers')) if role == 'member' else []
    token = accounts.create_invite(username, role, server_ids, g.user['id'])
    logs.audit('invite_created', username=username, role=role, servers=server_ids)
    return {'link': f'/invite/{token}', 'expires': time.time() + accounts.INVITE_FOR}, 201


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
    user = next(user for user in user_dicts() if user['id'] == user_id)
    logs.audit('user_changed', user_id=user_id, username=user['username'], role=role, servers=server_ids)
    return user


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


@bp.delete('/users/invites/<username>')
def remove_invite(username):
    """Withdraw an invite before it is used."""
    with get_db() as conn:
        if not conn.execute('DELETE FROM invites WHERE username = ?', (username,)).rowcount:
            abort(404, 'No such invite')
        conn.commit()
    logs.audit('invite_removed', username=username)
    return {}
