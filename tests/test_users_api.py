import re
import time

import pytest

from mcpanel import accounts, servers
from mcpanel.db import get_db

from .conftest import PASSWORD, make_user, sign_in, signed_in_client

USER_CALLS = [
    ('get', '/api/users', None),
    ('post', '/api/users', {'username': 'sam', 'role': 'member', 'servers': []}),
    ('put', '/api/users/1', {'role': 'member', 'servers': []}),
    ('post', '/api/users/1/reset', None),
    ('delete', '/api/users/1', None),
]
ONE_TIME_PASSWORD = re.compile(r'[0-9a-hjkmnp-tv-z]{4}(-[0-9a-hjkmnp-tv-z]{4}){3}')


def access(user_id):
    with get_db() as conn:
        return {row[0] for row in conn.execute('SELECT server FROM server_access WHERE user_id = ?', (user_id,))}


@pytest.mark.parametrize('method, path, body', USER_CALLS)
def test_members_get_403_on_user_calls(member, method, path, body):
    response = getattr(member, method)(path, json=body)
    assert response.status_code == 403 and response.get_json() == {'error': 'Admins only'}
    assert accounts.user_named('sam') is None and accounts.user_by_id(1)['role'] == 'member'


def test_admins_manage_members(admin, anon):
    make_servers('SMP', 'Creative')
    response = admin.post('/api/users', json={'username': 'sam', 'role': 'member', 'servers': ['smp']})
    assert response.status_code == 201 and response.get_json()['user']['servers'] == ['smp']
    sam = response.get_json()['user']['id']
    assert admin.put(f'/api/users/{sam}', json={'role': 'member', 'servers': ['creative']}).status_code == 200
    assert access(sam) == {'creative'}
    password = admin.post(f'/api/users/{sam}/reset').get_json()['one_time_password']
    assert sign_in(anon, 'sam', password).headers['Location'] == '/login/setup'
    assert admin.delete(f'/api/users/{sam}').status_code == 200 and accounts.user_by_id(sam) is None


@pytest.mark.parametrize('role', ['admin', 'platform_admin'])
def test_admins_cant_touch_admins(app, admin, role):
    kim = make_user('kim', role)
    sam = make_user('sam')
    calls = [
        admin.post('/api/users', json={'username': 'ana', 'role': role}),  # make one
        admin.put(f'/api/users/{sam["id"]}', json={'role': role}),  # promote a member
        admin.put(f'/api/users/{kim["id"]}', json={'role': 'member', 'servers': []}),  # demote one
        admin.put(f'/api/users/{kim["id"]}', json={'role': role}),  # or even save it as it is
        admin.post(f'/api/users/{kim["id"]}/reset'),
        admin.delete(f'/api/users/{kim["id"]}'),
    ]
    for response in calls:
        assert response.status_code == 403 and response.get_json() == {'error': 'Only platform admins manage admins'}
    assert accounts.user_named('ana') is None
    assert (accounts.user_by_id(sam['id'])['role'], accounts.user_by_id(kim['id'])['role']) == ('member', role)
    assert accounts.user_by_id(kim['id'])['totp_secret'] == kim['totp_secret']
    assert {user['username'] for user in admin.get('/api/users').get_json()['users']} == {'admin', 'kim', 'sam'}


@pytest.mark.parametrize('method, path, body', USER_CALLS)
def test_anonymous_gets_401(anon, method, path, body):
    assert getattr(anon, method)(path, json=body).status_code == 401


def test_list_users(platform_admin, app, clock):
    sam = make_user('sam')
    with get_db() as conn:
        conn.executemany('INSERT INTO server_access (user_id, server) VALUES (?, ?)', [(sam['id'], 'smp'), (sam['id'], 'creative')])
        conn.commit()
    accounts.create_user('kim', 'member', ['smp'])
    body = platform_admin.get('/api/users').get_json()
    users = {user['username']: user for user in body['users']}
    assert set(users) == {'owner', 'kim', 'sam'}
    assert users['owner']['role'] == 'platform_admin'
    assert users['sam']['servers'] == ['creative', 'smp'] and users['sam']['role'] == 'member'
    assert users['sam']['last_seen'] is None and users['sam']['created'] > 0
    assert users['owner']['servers'] == [] and users['owner']['last_seen'] > 0
    assert set(users['sam']) == {'id', 'username', 'role', 'servers', 'last_seen', 'created', 'one_time_password_expires'}
    # Waiting for a first sign-in: when the one-time password stops working, even once it has.
    assert users['sam']['one_time_password_expires'] is None and users['owner']['one_time_password_expires'] is None
    assert users['kim']['one_time_password_expires'] == pytest.approx(clock['now'] + 24 * 3600)
    assert users['kim']['servers'] == ['smp']
    clock['now'] += 25 * 3600
    expired = next(user for user in platform_admin.get('/api/users').get_json()['users'] if user['username'] == 'kim')
    assert expired['one_time_password_expires'] < clock['now']
    assert 'hash' not in str(body) and 'secret' not in str(body)


def make_servers(*names):
    for name in names:
        servers.create_server(name, 'VANILLA', 'LATEST', '', 1, None, True)


def test_add_a_user(platform_admin, anon):
    make_servers('SMP')
    response = platform_admin.post('/api/users', json={'username': 'Sam', 'role': 'member', 'servers': ['smp', 'smp']})
    assert response.status_code == 201
    body = response.get_json()
    assert set(body) == {'user', 'one_time_password'}
    user, password = body['user'], body['one_time_password']
    assert (user['username'], user['role'], user['servers'], user['last_seen']) == ('sam', 'member', ['smp'], None)
    assert user['one_time_password_expires'] > time.time() + 23 * 3600
    assert ONE_TIME_PASSWORD.fullmatch(password)
    row = accounts.user_by_id(user['id'])
    assert row['password_hash'].startswith('$argon2id$') and password not in row['password_hash']
    assert row['totp_secret'] is None
    assert sign_in(anon, 'sam', password).headers['Location'] == '/login/setup'
    assert password not in str(platform_admin.get('/api/users').get_json())  # shown only when made


@pytest.mark.parametrize('role', ['admin', 'platform_admin'])
def test_admins_are_added_without_servers(platform_admin, role):
    response = platform_admin.post('/api/users', json={'username': 'kim', 'role': role, 'servers': ['smp']})
    assert response.get_json()['user']['servers'] == [] and response.get_json()['user']['role'] == role
    assert accounts.servers_for(accounts.user_named('kim')) is None


@pytest.mark.parametrize('body, status', [
    ({'username': 'owner', 'role': 'member'}, 409),  # an existing user
    ({'username': 'no spaces', 'role': 'member'}, 400),
    ({'username': '', 'role': 'member'}, 400),
    ({'username': 'sam', 'role': 'owner'}, 400),
    ({'username': 'sam', 'role': 'member', 'servers': 'smp'}, 400),
    ({'username': 'sam', 'role': 'member', 'servers': ['../etc']}, 404),
    ({'username': 'sam', 'role': 'member', 'servers': ['later']}, 404),  # not a server yet
])
def test_bad_new_users(platform_admin, body, status):
    response = platform_admin.post('/api/users', json=body)
    assert response.status_code == status and response.get_json()['error']
    assert accounts.user_named('sam') is None


def test_change_a_member(platform_admin):
    make_servers('SMP', 'Creative', 'Survival')
    sam = make_user('sam')
    response = platform_admin.put(f'/api/users/{sam["id"]}', json={'role': 'member', 'servers': ['smp', 'creative']})
    assert response.status_code == 200 and response.get_json()['servers'] == ['creative', 'smp']
    platform_admin.put(f'/api/users/{sam["id"]}', json={'role': 'member', 'servers': ['survival']})
    assert access(sam['id']) == {'survival'}  # replaced, not added to
    response = platform_admin.put(f'/api/users/{sam["id"]}', json={'role': 'admin', 'servers': ['smp']})
    assert response.get_json()['role'] == 'admin' and access(sam['id']) == set()
    assert accounts.servers_for(accounts.user_by_id(sam['id'])) is None


def test_changes_take_effect_at_once(platform_admin, app):
    sam = signed_in_client(app, make_user('sam'))
    kim = make_user('kim', 'admin')
    assert sam.get('/api/users').status_code == 403 and sam.post('/api/servers', json={}).status_code == 403
    platform_admin.put(f'/api/users/{sam.user["id"]}', json={'role': 'admin'})
    assert sam.get('/api/users').status_code == 200 and sam.post('/api/servers', json={}).status_code == 400
    assert sam.delete(f'/api/users/{kim["id"]}').status_code == 403
    platform_admin.put(f'/api/users/{sam.user["id"]}', json={'role': 'platform_admin'})
    assert sam.delete(f'/api/users/{kim["id"]}').status_code == 200


@pytest.mark.parametrize('role', ['admin', 'member'])
def test_the_last_platform_admin_stays_one(platform_admin, role):
    kim = make_user('kim', 'admin')  # an admin doesn't count
    response = platform_admin.put(f'/api/users/{platform_admin.user["id"]}', json={'role': role})
    assert response.status_code == 409 and 'only platform admin' in response.get_json()['error']
    assert accounts.user_by_id(platform_admin.user['id'])['role'] == 'platform_admin'
    assert platform_admin.put(f'/api/users/{kim["id"]}', json={'role': 'platform_admin'}).status_code == 200
    assert platform_admin.put(f'/api/users/{platform_admin.user["id"]}', json={'role': role}).status_code == 200
    assert platform_admin.put(f'/api/users/{kim["id"]}', json={'role': 'member'}).status_code == 403  # no longer one


def test_a_platform_admin_removes_another(app, platform_admin):
    kim = signed_in_client(app, make_user('kim', 'platform_admin'))
    assert kim.delete(f'/api/users/{platform_admin.user["id"]}').status_code == 200
    assert platform_admin.get('/api/me').status_code == 401
    assert kim.delete(f'/api/users/{kim.user["id"]}').status_code == 400  # and the last one can't remove themselves


def test_change_unknown_user(platform_admin):
    assert platform_admin.put('/api/users/999', json={'role': 'member'}).status_code == 404


def test_remove_a_user_ends_their_sessions(platform_admin, app):
    sam = signed_in_client(app, make_user('sam'))
    assert sam.get('/api/me').status_code == 200
    assert platform_admin.delete(f'/api/users/{sam.user["id"]}').status_code == 200
    assert sam.get('/api/me').status_code == 401
    assert [user['username'] for user in platform_admin.get('/api/users').get_json()['users']] == ['owner']
    assert platform_admin.delete(f'/api/users/{sam.user["id"]}').status_code == 404


def test_you_cant_remove_yourself(platform_admin):
    response = platform_admin.delete(f'/api/users/{platform_admin.user["id"]}')
    assert response.status_code == 400 and accounts.user_by_id(platform_admin.user['id'])


def test_reset_a_users_sign_in(platform_admin, app, clock):
    sam = signed_in_client(app, make_user('sam'))
    accounts.new_recovery_codes(sam.user['id'])
    with get_db() as conn:  # locked, as after five wrong tries
        conn.execute('UPDATE users SET failed_tries = 3, locked_until = ? WHERE id = ?', (clock['now'] + 600, sam.user['id']))
        conn.commit()
    response = platform_admin.post(f'/api/users/{sam.user["id"]}/reset')
    assert response.status_code == 200 and set(response.get_json()) == {'one_time_password'}
    password = response.get_json()['one_time_password']
    assert ONE_TIME_PASSWORD.fullmatch(password)
    assert sam.get('/api/me').status_code == 401  # signed out everywhere
    row = accounts.user_by_id(sam.user['id'])
    assert (row['totp_secret'], row['failed_tries'], row['locked_until']) == (None, 0, 0)
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM recovery_codes').fetchone()[0] == 0
    assert sign_in(app.test_client(), 'sam', PASSWORD).status_code == 401  # the old password no longer works
    assert sign_in(app.test_client(), 'sam', password).headers['Location'] == '/login/setup'
    [listed] = [user for user in platform_admin.get('/api/users').get_json()['users'] if user['username'] == 'sam']
    assert listed['one_time_password_expires'] == pytest.approx(clock['now'] + 24 * 3600)


def test_you_cant_reset_your_own_sign_in(platform_admin):
    response = platform_admin.post(f'/api/users/{platform_admin.user["id"]}/reset')
    assert response.status_code == 400 and response.get_json()['error']
    assert platform_admin.get('/api/me').status_code == 200
    assert accounts.user_by_id(platform_admin.user['id'])['totp_secret'] == platform_admin.user['totp_secret']


def test_reset_unknown_user(platform_admin):
    assert platform_admin.post('/api/users/999/reset').status_code == 404
