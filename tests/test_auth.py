import logging
import re
import time

import pyotp
import pytest

from mcpanel import accounts, auth
from mcpanel.db import get_db

from .conftest import PASSWORD, make_user, sign_in


@pytest.fixture
def clock(monkeypatch):
    """time.time() stands still at clock['now'] until a test moves it."""
    clock = {'now': time.time()}
    monkeypatch.setattr(time, 'time', lambda: clock['now'])
    return clock


@pytest.fixture
def matt(app):
    return make_user('matt', 'admin')


def session_cookie(response):
    return next(h for h in response.headers.getlist('Set-Cookie') if h.startswith('mcpanel_session='))


def code_at(user, when):
    return pyotp.TOTP(user['totp_secret']).at(when)


def sign_in_code(client, user):
    return client.post('/login/code', data={'code': pyotp.TOTP(user['totp_secret']).now()})


def failed_tries(user):
    with get_db() as conn:
        return conn.execute('SELECT failed_tries FROM users WHERE id = ?', (user['id'],)).fetchone()[0]


def test_full_sign_in(anon, matt):
    response = anon.post('/login', data={'username': 'Matt', 'password': PASSWORD})
    assert response.status_code == 302 and response.headers['Location'] == '/login/code'
    assert anon.get('/api/me').status_code == 401  # the password alone signs nobody in
    page = anon.get('/login/code')
    assert b'6-digit code' in page.data and b'autocomplete="one-time-code"' in page.data
    response = anon.post('/login/code', data={'code': pyotp.TOTP(matt['totp_secret']).now()})
    assert response.status_code == 302 and response.headers['Location'] == '/'
    assert anon.get('/api/me').get_json()['username'] == 'matt'
    assert anon.get_cookie('mcpanel_signin') is None  # the halfway cookie is gone


def test_sign_in_returns_to_next(anon, matt):
    anon.post('/login', query_string={'next': '/servers/smp?tab=console'}, data={'username': 'matt', 'password': PASSWORD})
    response = sign_in_code(anon, matt)
    assert response.headers['Location'] == '/servers/smp?tab=console'


def test_wrong_password_and_unknown_username_look_the_same(anon, matt):
    wrong = anon.post('/login', data={'username': 'matt', 'password': 'wrong password!'})
    unknown = anon.post('/login', data={'username': 'nobody', 'password': PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert b'Wrong username or password.' in wrong.data and b'Wrong username or password.' in unknown.data
    assert wrong.data.replace(b'matt', b'') == unknown.data.replace(b'nobody', b'')
    assert 'mcpanel_session=' not in str(wrong.headers) and anon.get_cookie('mcpanel_signin') is None


def test_fifth_wrong_password_locks_the_account_for_15_minutes(anon, matt, clock):
    for _ in range(4):
        sign_in(anon, 'matt', 'wrong password!')
    assert failed_tries(matt) == 4
    response = sign_in(anon, 'matt', 'wrong password!')
    assert b'Wrong username or password.' in response.data  # not the lock message: that would say "matt" exists
    response = sign_in(anon, 'matt')  # the right password is refused while locked, and says why
    assert response.status_code == 401 and b'Try again in 15 minutes.' in response.data
    assert anon.get_cookie('mcpanel_signin') is None
    assert b'Wrong username or password.' in sign_in(anon, 'matt', 'still wrong!').data

    clock['now'] += 14 * 60
    assert b'Try again in 1 minute.' in sign_in(anon, 'matt').data
    clock['now'] += 60
    response = sign_in(anon, 'matt', code=code_at(matt, clock['now']))
    assert response.status_code == 302 and response.headers['Location'] == '/'
    assert failed_tries(matt) == 0


def test_wrong_codes_count_toward_the_lock(anon, matt, clock):
    sign_in(anon, 'matt', 'wrong password!')
    sign_in(anon, 'matt', 'wrong password!')
    anon.post('/login', data={'username': 'matt', 'password': PASSWORD})
    for _ in range(2):
        response = anon.post('/login/code', data={'code': '000000'})
        assert response.status_code == 401 and b"That code didn" in response.data
    response = anon.post('/login/code', data={'code': '000000'})
    assert b'locked this account' in response.data
    response = anon.post('/login/code', data={'code': code_at(matt, clock['now'])})  # even the right one, now
    assert b'locked this account' in response.data and anon.get('/api/me').status_code == 401


def test_a_success_resets_the_count(anon, matt):
    for _ in range(4):
        sign_in(anon, 'matt', 'wrong password!')
    assert sign_in(anon, 'matt').status_code == 302
    assert failed_tries(matt) == 0


def test_a_totp_code_is_refused_the_second_time(app, matt, clock):
    code = code_at(matt, clock['now'])
    assert sign_in(app.test_client(), 'matt', code=code).headers['Location'] == '/'
    response = sign_in(app.test_client(), 'matt', code=code)
    assert response.status_code == 401 and b"That code didn" in response.data


def test_a_recovery_code_works_once(app, matt):
    codes = accounts.new_recovery_codes(matt['id'])
    client = app.test_client()
    client.post('/login', data={'username': 'matt', 'password': PASSWORD})
    page = client.get('/login/code?recovery=1')
    assert b'Recovery code' in page.data and b'Use your authenticator app' in page.data
    response = client.post('/login/code?recovery=1', data={'code': codes[0].upper()})
    assert response.status_code == 302 and client.get('/api/me').get_json()['recovery_codes_left'] == 9

    again = app.test_client()
    again.post('/login', data={'username': 'matt', 'password': PASSWORD})
    response = again.post('/login/code?recovery=1', data={'code': codes[0]})
    assert response.status_code == 401 and b"That recovery code didn" in response.data


def test_the_code_step_needs_the_password_first(anon, matt):
    assert anon.get('/login/code').headers['Location'] == '/login'
    response = anon.post('/login/code', data={'code': pyotp.TOTP(matt['totp_secret']).now()})
    assert response.status_code == 401 and anon.get('/api/me').status_code == 401


def test_a_pending_sign_in_expires_after_5_minutes(anon, matt, clock):
    anon.post('/login', data={'username': 'matt', 'password': PASSWORD})
    clock['now'] += 5 * 60 + 1
    response = anon.post('/login/code', data={'code': code_at(matt, clock['now'])})
    assert response.status_code == 401 and b'That took too long' in response.data
    assert anon.get('/api/me').status_code == 401
    assert anon.get('/login/code').headers['Location'] == '/login'


def test_a_wrong_try_holds_off_that_address_for_a_second(app, matt, monkeypatch):
    now = 1000.0
    monkeypatch.setattr(auth, 'WRONG_TRY_WAIT', 1)
    monkeypatch.setattr(auth.time, 'monotonic', lambda: now)
    monkeypatch.setattr(auth.time, 'sleep', lambda seconds: None)
    stranger = app.test_client()
    stranger.environ_base['REMOTE_ADDR'] = '203.0.113.9'
    assert sign_in(stranger, 'nobody', 'wrong password!').status_code == 401
    response = sign_in(stranger, 'matt')  # even the right one, from that address
    assert response.status_code == 429 and b'Wait a moment' in response.data
    assert failed_tries(matt) == 0  # refused unchecked
    assert sign_in(app.test_client(), 'matt').status_code == 302  # a stranger's tries hold off nobody else
    now += 1
    assert sign_in(stranger, 'matt').status_code == 302


def test_remembered_cookie(anon, matt):
    cookie = session_cookie(sign_in(anon, 'matt', remember=True))
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Path=/' in cookie
    assert f'Max-Age={30 * 24 * 3600}' in cookie and 'Secure' not in cookie
    with get_db() as conn:
        row = conn.execute('SELECT created, expires FROM sessions').fetchone()
    assert row['expires'] - row['created'] == pytest.approx(30 * 24 * 3600)


def test_unremembered_cookie_ends_with_the_browser(anon, matt):
    cookie = session_cookie(sign_in(anon, 'matt'))
    assert 'Max-Age' not in cookie and 'Expires' not in cookie and 'HttpOnly' in cookie
    with get_db() as conn:
        row = conn.execute('SELECT created, expires FROM sessions').fetchone()
    assert row['expires'] - row['created'] == pytest.approx(24 * 3600)


def test_cookies_are_secure_behind_https(app, matt):
    client = app.test_client()
    client.environ_base['HTTP_X_FORWARDED_PROTO'] = 'https'
    halfway = client.post('/login', data={'username': 'matt', 'password': PASSWORD})
    assert 'Secure' in halfway.headers['Set-Cookie']  # mcpanel_signin, the sign-in halfway through
    assert 'Secure' in session_cookie(sign_in(client, 'matt'))


def test_session_stores_only_a_hash_of_the_token(anon, matt):
    sign_in(anon, 'matt')
    token = anon.get_cookie('mcpanel_session').value
    with get_db() as conn:
        assert conn.execute('SELECT token_hash FROM sessions').fetchone()[0] == accounts.hashed(token)


def test_logout_ends_the_session(app, matt):
    client = app.test_client()
    sign_in(client, 'matt')
    token = client.get_cookie('mcpanel_session').value
    response = client.post('/logout')
    assert response.headers['Location'] == '/login'
    assert client.get('/api/me').status_code == 401
    other = app.test_client()
    other.set_cookie('mcpanel_session', token)  # a copy of the cookie doesn't work either
    assert other.get('/api/me').status_code == 401


def test_anonymous_api_calls_get_401(anon):
    response = anon.get('/api/me')
    assert response.status_code == 401 and response.get_json() == {'error': 'Sign in first'}
    assert anon.post('/api/users/invite', json={}).status_code == 401


def test_anonymous_pages_go_to_sign_in(anon):
    assert anon.get('/').headers['Location'] == '/login?next=/'
    assert anon.get('/servers/smp?tab=console').headers['Location'] == '/login?next=/servers/smp?tab%3Dconsole'


def test_open_routes(anon):
    assert anon.get('/login').status_code == 200
    assert anon.get('/healthz').status_code == 200
    assert anon.get('/static/css/style.css').status_code == 200
    assert anon.get('/invite/nope').status_code == 404


@pytest.mark.parametrize('target, expected', [
    ('/servers/smp', '/servers/smp'),
    ('//evil.example/', '/'),
    ('https://evil.example/', '/'),
    ('/\\evil.example', '/'),
    ('/\t/evil.example', '/'),
    ('/\r\nSet-Cookie: x=1', '/'),
])
def test_sign_in_returns_only_to_this_site(anon, matt, target, expected):
    anon.post('/login', query_string={'next': target}, data={'username': 'matt', 'password': PASSWORD})
    assert sign_in_code(anon, matt).headers['Location'] == expected


def test_signed_in_login_page_moves_on(anon, matt):
    sign_in(anon, 'matt')
    assert anon.get('/login?next=/servers/smp').headers['Location'] == '/servers/smp'


def test_login_page_says_how_to_make_the_first_account(anon):
    assert b'flask --app app invite &lt;name&gt; --admin' in anon.get('/login').data
    make_user('matt')
    assert b'flask --app app invite' not in anon.get('/login').data


def test_login_page_fields(anon):
    page = anon.get('/login').data
    assert b'autocomplete="username"' in page and b'autocomplete="current-password"' in page
    assert b'href="#i-cube"' in page and b'mcpanel</span>' in page


def test_no_secret_reaches_the_logs(app, matt, caplog):
    caplog.set_level(logging.INFO)
    client = app.test_client()
    codes = accounts.new_recovery_codes(matt['id'])
    sign_in(client, 'matt', 'a wrong password')
    code = pyotp.TOTP(matt['totp_secret']).now()
    assert sign_in(client, 'matt', code=code).status_code == 302
    client.post('/login', data={'username': 'matt', 'password': PASSWORD})
    client.post('/login/code?recovery=1', data={'code': codes[0]})
    token = client.get_cookie('mcpanel_session').value
    client.post('/logout')
    logged = '\n'.join(str(vars(record)) for record in caplog.records)
    assert 'sign_in' in logged
    for secret in (PASSWORD, 'a wrong password', code, matt['totp_secret'], token, accounts.hashed(token), codes[0]):
        assert secret not in logged


# Invites

@pytest.fixture
def invite_link(app):
    return '/invite/' + accounts.create_invite('sam', 'member', ['smp'], None)


def invite_secret(username='sam'):
    with get_db() as conn:
        return conn.execute('SELECT totp_secret FROM invites WHERE username = ?', (username,)).fetchone()[0]


def accept(client, link, password=PASSWORD, again=None, code=None):
    code = code or pyotp.TOTP(invite_secret()).now()
    return client.post(link, data={'password': password, 'again': again or password, 'code': code})


def test_invite_page(anon, invite_link):
    page = anon.get(invite_link)
    assert page.status_code == 200
    secret = invite_secret()
    html = page.get_data(as_text=True)
    assert '<svg' in html and ' '.join(re.findall('....', secret)) in html
    assert 'autocomplete="new-password"' in html and 'autocomplete="one-time-code"' in html
    assert anon.get(invite_link).get_data(as_text=True) == html  # the same QR code on a reload


def test_accepting_an_invite(app, anon, invite_link, clock):
    response = accept(anon, invite_link, code=pyotp.TOTP(invite_secret()).at(clock['now']))
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    codes = re.findall(r'<li>([0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4})</li>', response.get_data(as_text=True))
    assert len(codes) == 10 and b'Continue to mcpanel' in response.data
    assert 'Max-Age' not in session_cookie(response)  # signed in, not remembered
    assert anon.get('/api/me').get_json() == {'username': 'sam', 'role': 'member', 'recovery_codes_left': 10}
    sam = accounts.user_named('sam')
    assert accounts.servers_for(sam) == {'smp'}
    assert anon.get(invite_link).status_code == 404  # single use
    assert accept(app.test_client(), invite_link, code='123456').status_code == 404
    # The code used to set up 2FA can't be replayed to sign in.
    response = sign_in(app.test_client(), 'sam', code=pyotp.TOTP(sam['totp_secret']).at(clock['now']))
    assert response.status_code == 401


@pytest.mark.parametrize('form, message', [
    ({'password': 'too short'}, b'at least 12 characters'),
    ({'again': 'something else entirely'}, b'The two passwords'),
    ({'code': '000000'}, b"That code didn"),
])
def test_invite_form_errors(anon, invite_link, form, message):
    response = accept(anon, invite_link, **form)
    assert response.status_code == 400 and message in response.data
    assert b'<svg' in response.data and anon.get('/api/me').status_code == 401
    assert accounts.user_named('sam') is None
    assert anon.get(invite_link).status_code == 200  # still usable


def test_expired_invite(anon, invite_link, clock):
    clock['now'] += 24 * 3600 + 1
    response = anon.get(invite_link)
    assert response.status_code == 404 and b'ask an admin for a new one' in response.data
    assert accept(anon, invite_link, code=pyotp.TOTP(invite_secret()).at(clock['now'])).status_code == 404


def test_newer_invite_replaces_the_old(anon, invite_link):
    newer = '/invite/' + accounts.create_invite('sam', 'admin', [], None)
    assert anon.get(invite_link).status_code == 404
    assert anon.get(newer).status_code == 200


def test_invite_command_prints_a_working_link(app):
    result = app.test_cli_runner().invoke(args=['invite', 'Matt', '--admin'])
    assert result.exit_code == 0, result.output
    assert '24 hours' in result.output
    link = re.search(r'/invite/\S+', result.output).group()
    client = app.test_client()
    with get_db() as conn:
        secret = conn.execute("SELECT totp_secret FROM invites WHERE username = 'matt'").fetchone()[0]
    response = client.post(link, data={'password': PASSWORD, 'again': PASSWORD, 'code': pyotp.TOTP(secret).now()})
    assert response.status_code == 200
    assert client.get('/api/me').get_json()['role'] == 'admin'


def test_invite_command_refuses_an_existing_username(app):
    make_user('matt')
    result = app.test_cli_runner().invoke(args=['invite', 'matt'])
    assert result.exit_code != 0 and 'already a user called matt' in result.output
    result = app.test_cli_runner().invoke(args=['invite', 'not ok'])
    assert result.exit_code != 0 and 'username' in result.output
