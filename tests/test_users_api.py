import time

import pytest

from mcpanel import accounts
from mcpanel.db import get_db

from .conftest import make_user, signed_in_client

ADMIN_CALLS = [
    ('get', '/api/users', None),
    ('post', '/api/users/invite', {'username': 'sam', 'role': 'member', 'servers': []}),
    ('put', '/api/users/1', {'role': 'member', 'servers': []}),
    ('delete', '/api/users/1', None),
    ('delete', '/api/users/invites/sam', None),
]


def access(user_id):
    with get_db() as conn:
        return {row[0] for row in conn.execute('SELECT server FROM server_access WHERE user_id = ?', (user_id,))}


@pytest.mark.parametrize('method, path, body', ADMIN_CALLS)
def test_members_get_403_on_admin_calls(member, method, path, body):
    response = getattr(member, method)(path, json=body)
    assert response.status_code == 403 and response.get_json() == {'error': 'Admins only'}


@pytest.mark.parametrize('method, path, body', ADMIN_CALLS)
def test_anonymous_gets_401(anon, method, path, body):
    assert getattr(anon, method)(path, json=body).status_code == 401


def test_list_users(admin, app):
    sam = make_user('sam')
    with get_db() as conn:
        conn.executemany('INSERT INTO server_access (user_id, server) VALUES (?, ?)', [(sam['id'], 'smp'), (sam['id'], 'creative')])
        conn.commit()
    accounts.create_invite('kim', 'member', ['smp'], admin.user['id'])
    body = admin.get('/api/users').get_json()
    users = {user['username']: user for user in body['users']}
    assert set(users) == {'admin', 'sam'}
    assert users['sam']['servers'] == ['creative', 'smp'] and users['sam']['role'] == 'member'
    assert users['sam']['last_seen'] is None and users['sam']['created'] > 0
    assert users['admin']['servers'] == [] and users['admin']['last_seen'] > 0
    assert set(users['sam']) == {'id', 'username', 'role', 'servers', 'last_seen', 'created'}
    [invite] = body['invites']
    assert invite['username'] == 'kim' and invite['role'] == 'member' and invite['servers'] == ['smp']
    assert invite['expires'] > time.time()
    assert 'token' not in str(body) and 'secret' not in str(body)


def test_invite(admin, anon):
    response = admin.post('/api/users/invite', json={'username': 'Sam', 'role': 'member', 'servers': ['smp', 'smp']})
    assert response.status_code == 201
    link = response.get_json()['link']
    assert link.startswith('/invite/') and response.get_json()['expires'] > time.time()
    assert anon.get(link).status_code == 200
    [invite] = admin.get('/api/users').get_json()['invites']
    assert (invite['username'], invite['servers']) == ('sam', ['smp'])


def test_admin_invites_carry_no_servers(admin):
    admin.post('/api/users/invite', json={'username': 'kim', 'role': 'admin', 'servers': ['smp']})
    assert admin.get('/api/users').get_json()['invites'][0]['servers'] == []


def test_a_new_invite_replaces_the_old(admin, anon):
    first = admin.post('/api/users/invite', json={'username': 'sam', 'role': 'member'}).get_json()['link']
    second = admin.post('/api/users/invite', json={'username': 'sam', 'role': 'admin'}).get_json()['link']
    assert anon.get(first).status_code == 404 and anon.get(second).status_code == 200
    assert len(admin.get('/api/users').get_json()['invites']) == 1


@pytest.mark.parametrize('body, status', [
    ({'username': 'admin', 'role': 'member'}, 409),  # an existing user
    ({'username': 'no spaces', 'role': 'member'}, 400),
    ({'username': '', 'role': 'member'}, 400),
    ({'username': 'sam', 'role': 'owner'}, 400),
    ({'username': 'sam', 'role': 'member', 'servers': 'smp'}, 400),
    ({'username': 'sam', 'role': 'member', 'servers': ['../etc']}, 404),
])
def test_bad_invites(admin, body, status):
    response = admin.post('/api/users/invite', json=body)
    assert response.status_code == status and response.get_json()['error']


def test_change_a_member(admin):
    sam = make_user('sam')
    response = admin.put(f'/api/users/{sam["id"]}', json={'role': 'member', 'servers': ['smp', 'creative']})
    assert response.status_code == 200 and response.get_json()['servers'] == ['creative', 'smp']
    admin.put(f'/api/users/{sam["id"]}', json={'role': 'member', 'servers': ['survival']})
    assert access(sam['id']) == {'survival'}  # replaced, not added to
    response = admin.put(f'/api/users/{sam["id"]}', json={'role': 'admin', 'servers': ['smp']})
    assert response.get_json()['role'] == 'admin' and access(sam['id']) == set()
    assert accounts.servers_for(accounts.user_by_id(sam['id'])) is None


def test_changes_take_effect_at_once(admin, app):
    sam = signed_in_client(app, make_user('sam'))
    assert sam.get('/api/users').status_code == 403
    admin.put(f'/api/users/{sam.user["id"]}', json={'role': 'admin'})
    assert sam.get('/api/users').status_code == 200


def test_the_last_admin_stays_an_admin(admin):
    response = admin.put(f'/api/users/{admin.user["id"]}', json={'role': 'member'})
    assert response.status_code == 409 and 'only admin' in response.get_json()['error']
    assert accounts.user_by_id(admin.user['id'])['role'] == 'admin'
    kim = make_user('kim', 'admin')
    assert admin.put(f'/api/users/{admin.user["id"]}', json={'role': 'member'}).status_code == 200
    assert admin.put(f'/api/users/{kim["id"]}', json={'role': 'member'}).status_code == 403  # no longer an admin


def test_change_unknown_user(admin):
    assert admin.put('/api/users/999', json={'role': 'member'}).status_code == 404


def test_remove_a_user_ends_their_sessions(admin, app):
    sam = signed_in_client(app, make_user('sam'))
    assert sam.get('/api/me').status_code == 200
    assert admin.delete(f'/api/users/{sam.user["id"]}').status_code == 200
    assert sam.get('/api/me').status_code == 401
    assert [user['username'] for user in admin.get('/api/users').get_json()['users']] == ['admin']
    assert admin.delete(f'/api/users/{sam.user["id"]}').status_code == 404


def test_you_cant_remove_yourself(admin):
    response = admin.delete(f'/api/users/{admin.user["id"]}')
    assert response.status_code == 400 and accounts.user_by_id(admin.user['id'])


def test_remove_an_invite(admin, anon):
    link = admin.post('/api/users/invite', json={'username': 'sam', 'role': 'member'}).get_json()['link']
    assert admin.delete('/api/users/invites/sam').status_code == 200
    assert anon.get(link).status_code == 404
    assert admin.get('/api/users').get_json()['invites'] == []
    assert admin.delete('/api/users/invites/sam').status_code == 404
