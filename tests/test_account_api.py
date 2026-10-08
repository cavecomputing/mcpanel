import pytest

from mcpanel import accounts, auth
from mcpanel.db import get_db

from .conftest import PASSWORD, sign_in, signed_in_client


def test_me(member):
    assert member.get('/api/me').get_json() == {'username': 'member', 'role': 'member', 'recovery_codes_left': 0}
    accounts.new_recovery_codes(member.user['id'])
    assert member.get('/api/me').get_json()['recovery_codes_left'] == 10


def test_sessions_list_marks_this_device(app, member):
    other = signed_in_client(app, member.user)
    sessions = member.get('/api/account/sessions').get_json()['sessions']
    assert len(sessions) == 2 and [s['current'] for s in sessions].count(True) == 1
    current = next(s for s in sessions if s['current'])
    assert len(current['id']) == 16 and set(current) == {'id', 'created', 'last_seen', 'ip', 'user_agent', 'current'}
    assert other.get_cookie('mcpanel_session').value not in str(sessions)


def test_signed_in_devices_show_where_they_came_from(app, member):
    client = app.test_client()
    client.environ_base.update(REMOTE_ADDR='203.0.113.7', HTTP_USER_AGENT='Firefox on a phone')
    sign_in(client, 'member')
    [session] = [s for s in client.get('/api/account/sessions').get_json()['sessions'] if s['current']]
    assert (session['ip'], session['user_agent']) == ('203.0.113.7', 'Firefox on a phone')


def test_revoke_a_session(app, member):
    other = signed_in_client(app, member.user)
    sessions = member.get('/api/account/sessions').get_json()['sessions']
    theirs = next(s for s in sessions if not s['current'])
    assert member.delete(f'/api/account/sessions/{theirs["id"]}').status_code == 200
    assert other.get('/api/me').status_code == 401
    assert member.get('/api/me').status_code == 200
    assert member.delete(f'/api/account/sessions/{theirs["id"]}').status_code == 404


def test_cant_revoke_someone_elses_session(admin, member):
    [theirs] = admin.get('/api/account/sessions').get_json()['sessions']
    assert member.delete(f'/api/account/sessions/{theirs["id"]}').status_code == 404
    assert admin.get('/api/me').status_code == 200


def test_sign_out_others(app, member, admin):
    others = [signed_in_client(app, member.user) for _ in range(2)]
    assert member.post('/api/account/sessions/sign-out-others').status_code == 200
    assert all(other.get('/api/me').status_code == 401 for other in others)
    assert member.get('/api/me').status_code == 200 and admin.get('/api/me').status_code == 200
    assert len(member.get('/api/account/sessions').get_json()['sessions']) == 1


def test_change_password(app, member):
    other = signed_in_client(app, member.user)
    response = member.post('/api/account/password', json={'current': PASSWORD, 'new': 'a brand new password'})
    assert response.status_code == 200
    assert member.get('/api/me').status_code == 200  # this device stays signed in
    assert other.get('/api/me').status_code == 401  # every other one is signed out
    assert sign_in(app.test_client(), 'member').status_code == 401
    assert sign_in(app.test_client(), 'member', 'a brand new password').status_code == 302


@pytest.mark.parametrize('body, status', [
    ({'current': 'not my password', 'new': 'a brand new password'}, 403),
    ({'new': 'a brand new password'}, 403),
    ({'current': PASSWORD, 'new': 'too short'}, 400),
    ({'current': PASSWORD}, 400),
    ({'current': PASSWORD, 'new': 1234567890123}, 400),
])
def test_bad_password_changes(app, member, body, status):
    other = signed_in_client(app, member.user)
    response = member.post('/api/account/password', json=body)
    assert response.status_code == status and response.get_json()['error']
    assert other.get('/api/me').status_code == 200
    assert accounts.password_matches(accounts.user_by_id(member.user['id']), PASSWORD)


def test_wrong_password_goes_through_the_sign_in_throttle(member, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(auth, 'WRONG_TRY_WAIT', 1)
    monkeypatch.setattr(auth.time, 'monotonic', lambda: now)
    monkeypatch.setattr(auth.time, 'sleep', lambda seconds: None)
    assert member.post('/api/account/recovery-codes', json={'password': 'nope'}).status_code == 403
    assert member.post('/api/account/recovery-codes', json={'password': PASSWORD}).status_code == 429


def test_new_recovery_codes_need_the_password(app, member):
    old = accounts.new_recovery_codes(member.user['id'])
    response = member.post('/api/account/recovery-codes', json={'password': 'not my password'})
    assert response.status_code == 403
    assert member.get('/api/me').get_json()['recovery_codes_left'] == 10
    response = member.post('/api/account/recovery-codes', json={'password': PASSWORD})
    codes = response.get_json()['codes']
    assert response.status_code == 200 and len(codes) == 10 and not set(codes) & set(old)
    assert not accounts.use_recovery_code(member.user['id'], old[0])
    assert accounts.use_recovery_code(member.user['id'], codes[0])
    with get_db() as conn:
        assert conn.execute('SELECT COUNT(*) FROM recovery_codes').fetchone()[0] == 9
