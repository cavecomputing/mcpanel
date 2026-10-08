import logging
import re

import pyotp
import pytest

from mcpanel import accounts, auth
from mcpanel.db import get_db

from .conftest import PASSWORD, make_user, sign_in


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


def test_fifth_wrong_password_locks_the_account_for_15_minutes(anon, matt, clock, monkeypatch):
    for _ in range(4):
        sign_in(anon, 'matt', 'wrong password!')
    assert failed_tries(matt) == 4
    wrong = sign_in(anon, 'matt', 'wrong password!')
    # Not the lock message, which would say "matt" exists; the rule instead.
    assert b'Wrong username or password. 5 wrong tries in a row lock an account for 15 minutes.' in wrong.data
    # While locked, the right password gets the very same answer, checked against the dummy hash so
    # it takes as long: the lock doesn't tell a guesser when they got it right.
    real_password_matches, checked = accounts.password_matches, []
    monkeypatch.setattr(accounts, 'password_matches',
                        lambda user, password: checked.append(user) or real_password_matches(user, password))
    right = sign_in(anon, 'matt')
    assert right.status_code == 401 and right.data == wrong.data and checked == [None]
    assert anon.get_cookie('mcpanel_signin') is None

    clock['now'] += 14 * 60
    assert sign_in(anon, 'matt').data == wrong.data
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
    assert anon.post('/api/users', json={}).status_code == 401


def test_anonymous_pages_go_to_sign_in(anon):
    assert anon.get('/').headers['Location'] == '/login?next=/'
    assert anon.get('/servers/smp?tab=console').headers['Location'] == '/login?next=/servers/smp?tab%3Dconsole'


def test_open_routes(anon):
    assert anon.get('/login').status_code == 200
    assert anon.get('/healthz').status_code == 200
    assert anon.get('/static/css/style.css').status_code == 200
    assert anon.get('/login/setup').headers['Location'] == '/login'  # not /login?next=: it is a sign-in page


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
    assert b'flask --app app create-user &lt;name&gt; --admin' in anon.get('/login').data
    make_user('matt')
    assert b'flask --app app create-user' not in anon.get('/login').data


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


# The first sign-in: the one-time password an admin handed out, then the set-up

NEW_PASSWORD = 'my very own password'


@pytest.fixture
def sam(app):
    """A member just added, who may use smp, as {'id', 'username', 'one_time_password'}."""
    user_id, password = accounts.create_user('sam', 'member', ['smp'])
    return {'id': user_id, 'username': 'sam', 'one_time_password': password}


def first_sign_in(client, user, remember=False):
    return sign_in(client, user['username'], user['one_time_password'], remember=remember)


def shown_secret(page):
    """The TOTP secret the set-up page shows as its key."""
    return re.search(r'totp-key">([A-Z2-7 ]+)</code>', page.get_data(as_text=True)).group(1).replace(' ', '')


def set_up(client, password=NEW_PASSWORD, again=None, code=None):
    """Post the set-up form. code defaults to the current one for the secret the page shows."""
    code = code or pyotp.TOTP(shown_secret(client.get('/login/setup'))).now()
    return client.post('/login/setup', data={'password': password, 'again': again or password, 'code': code})


def user_row(username):
    with get_db() as conn:
        return conn.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()


def test_a_first_sign_in_goes_to_the_set_up_not_the_code(anon, sam):
    response = first_sign_in(anon, sam)
    assert response.status_code == 302 and response.headers['Location'] == '/login/setup'
    assert anon.get('/api/me').status_code == 401  # the one-time password alone signs nobody in
    page = anon.get('/login/setup')
    assert page.status_code == 200 and page.headers['Cache-Control'] == 'no-store'
    html = page.get_data(as_text=True)
    secret = shown_secret(page)
    assert len(secret) == 32 and '<svg' in html and '<b>sam</b>' in html
    assert 'autocomplete="new-password"' in html and 'autocomplete="one-time-code"' in html
    assert anon.get('/login/setup').get_data(as_text=True) == html  # the same QR code on a reload
    assert user_row('sam')['totp_secret'] is None  # not stored until the set-up is done
    assert anon.get('/login/code').headers['Location'] == '/login'  # no code step without TOTP


def test_setting_up(app, anon, sam, clock):
    first_sign_in(anon, sam)
    other_tab = app.test_client()
    first_sign_in(other_tab, sam)
    secret = shown_secret(anon.get('/login/setup'))
    code = pyotp.TOTP(secret).at(clock['now'])
    response = anon.post('/login/setup', data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD, 'code': code})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    codes = re.findall(r'<li>([0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4})</li>', response.get_data(as_text=True))
    assert len(codes) == 10 and b'Continue to mcpanel' in response.data and b'href="/"' in response.data
    assert 'Max-Age' not in session_cookie(response)  # signed in, not remembered
    assert anon.get_cookie('mcpanel_signin') is None  # the halfway cookie is gone
    assert anon.get('/api/me').get_json() == {'username': 'sam', 'role': 'member', 'recovery_codes_left': 10}
    row = user_row('sam')
    assert (row['totp_secret'], row['totp_last_step']) == (secret, int(clock['now']) // 30)
    assert accounts.servers_for(row) == {'smp'} and accounts.password_matches(row, NEW_PASSWORD)
    assert accounts.use_recovery_code(sam['id'], codes[0])

    # Done once: the other tab, the one-time password and the set-up's code no longer work.
    response = other_tab.post('/login/setup', data={'password': 'another password!', 'again': 'another password!', 'code': code})
    assert response.status_code == 401 and b'This account changed while you were signing in' in response.data
    assert sign_in(app.test_client(), 'sam', sam['one_time_password']).status_code == 401
    assert sign_in(app.test_client(), 'sam', NEW_PASSWORD, code=code).status_code == 401
    clock['now'] += 30
    response = sign_in(app.test_client(), 'sam', NEW_PASSWORD, code=pyotp.TOTP(secret).at(clock['now']))
    assert response.status_code == 302 and response.headers['Location'] == '/'


def test_setting_up_keeps_the_device_signed_in_when_asked(anon, sam):
    first_sign_in(anon, sam, remember=True)
    assert f'Max-Age={30 * 24 * 3600}' in session_cookie(set_up(anon))


def test_setting_up_returns_to_next(anon, sam):
    anon.post('/login', query_string={'next': '/#/account'}, data={'username': 'sam', 'password': sam['one_time_password']})
    assert b'href="/#/account"' in set_up(anon).data


@pytest.mark.parametrize('form, message', [
    ({'password': 'too short'}, b'at least 12 characters'),
    ({'again': 'something else entirely'}, b'The two passwords'),
    ({'password': 'ONE TIME'}, b'not the one-time one'),
    ({'code': '000000'}, b"That code didn"),
])
def test_set_up_form_errors(anon, sam, form, message):
    first_sign_in(anon, sam)
    if form.get('password') == 'ONE TIME':
        form = {'password': sam['one_time_password']}
    response = set_up(anon, **form)
    assert response.status_code == 400 and message in response.data
    assert b'<svg' in response.data and response.headers['Cache-Control'] == 'no-store'
    assert anon.get('/api/me').status_code == 401
    row = user_row('sam')
    assert row['totp_secret'] is None and row['failed_tries'] == 0  # nothing to guess here, so no count
    assert set_up(anon).status_code == 200  # still there to finish


def test_the_set_up_needs_the_one_time_password_first(anon, sam):
    assert anon.get('/login/setup').headers['Location'] == '/login'
    response = anon.post('/login/setup', data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD, 'code': '123456'})
    assert response.status_code == 401 and b'That took too long' in response.data
    assert user_row('sam')['totp_secret'] is None


def test_an_account_with_totp_never_reaches_the_set_up(anon, matt):
    """Else a password alone could replace the account's two-factor sign-in."""
    anon.post('/login', data={'username': 'matt', 'password': PASSWORD})
    assert anon.get('/login/setup').headers['Location'] == '/login'
    anon.post('/login', data={'username': 'matt', 'password': PASSWORD})
    secret = pyotp.random_base32()
    response = anon.post('/login/setup', data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD,
                                               'code': pyotp.TOTP(secret).now()})
    assert response.status_code == 401 and anon.get('/api/me').status_code == 401
    assert user_row('matt')['totp_secret'] == matt['totp_secret']


def test_a_pending_set_up_expires_after_15_minutes(anon, sam, clock):
    first_sign_in(anon, sam)
    clock['now'] += 14 * 60  # well past the code step's 5 minutes
    assert anon.get('/login/setup').status_code == 200
    clock['now'] += 60 + 1
    response = anon.post('/login/setup', data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD, 'code': '123456'})
    assert response.status_code == 401 and b'That took too long' in response.data
    assert anon.get('/login/setup').headers['Location'] == '/login'
    assert user_row('sam')['totp_secret'] is None


def test_a_one_time_password_works_for_24_hours(app, sam, clock):
    clock['now'] += 24 * 3600 - 1
    assert first_sign_in(app.test_client(), sam).headers['Location'] == '/login/setup'
    clock['now'] += 2
    client = app.test_client()
    right = first_sign_in(client, sam)
    assert right.status_code == 401 and b'This one-time password has expired. Ask an admin for a new one.' in right.data
    assert client.get_cookie('mcpanel_signin') is None
    wrong = sign_in(client, 'sam', 'wrong password!')
    assert b'Wrong username or password.' in wrong.data and b'expired' not in wrong.data  # only to whoever knows it
    assert user_row('sam')['failed_tries'] == 1  # the right one, expired, isn't a wrong try


def test_failed_tries_that_dont_count_toward_the_lock_are_audited(app, sam, clock, caplog):
    """A wrong code at the set-up, and the right one-time password once it has expired."""
    caplog.set_level(logging.INFO)
    client = app.test_client()
    first_sign_in(client, sam)
    set_up(client, code='000000')
    clock['now'] += 24 * 3600 + 1
    first_sign_in(app.test_client(), sam)
    failed = [(record.username, record.step) for record in caplog.records if record.getMessage() == 'sign_in_failed']
    assert failed == [('sam', 'setup'), ('sam', 'expired one-time password')]
    assert user_row('sam')['failed_tries'] == 0


def test_a_locked_new_account_refuses_its_one_time_password(anon, sam, clock):
    for _ in range(5):
        sign_in(anon, 'sam', 'wrong password!')
    right = first_sign_in(anon, sam)
    assert right.status_code == 401 and b'Wrong username or password.' in right.data
    clock['now'] += 24 * 3600  # still locked, and expired too: still the same answer
    with get_db() as conn:
        conn.execute('UPDATE users SET locked_until = ? WHERE id = ?', (clock['now'] + 60, sam['id']))
        conn.commit()
    assert first_sign_in(anon, sam).data == right.data


def test_a_reset_ends_a_set_up_under_way(app, anon, sam):
    first_sign_in(anon, sam)
    secret = shown_secret(anon.get('/login/setup'))
    new_password = accounts.reset_sign_in(sam['id'])
    response = anon.post('/login/setup', data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD,
                                               'code': pyotp.TOTP(secret).now()})
    assert response.status_code == 401 and b'This account changed while you were signing in' in response.data
    assert user_row('sam')['totp_secret'] is None
    assert first_sign_in(app.test_client(), dict(sam, one_time_password=new_password)).headers['Location'] == '/login/setup'


def test_a_reset_ends_a_code_step_under_way(anon, matt):
    anon.post('/login', data={'username': 'matt', 'password': PASSWORD})
    accounts.reset_sign_in(matt['id'])
    response = sign_in_code(anon, matt)
    assert response.status_code == 401 and b'This account changed while you were signing in' in response.data


def test_set_up_cookies_are_secure_behind_https(app, sam):
    client = app.test_client()
    client.environ_base['HTTP_X_FORWARDED_PROTO'] = 'https'
    assert 'Secure' in first_sign_in(client, sam).headers['Set-Cookie']
    assert 'Secure' in session_cookie(set_up(client))


def test_set_up_refuses_other_sites(anon, sam):
    first_sign_in(anon, sam)
    response = anon.post('/login/setup', headers={'Sec-Fetch-Site': 'cross-site'},
                         data={'password': NEW_PASSWORD, 'again': NEW_PASSWORD, 'code': '123456'})
    assert response.status_code == 403


def test_create_user_command(app):
    result = app.test_cli_runner().invoke(args=['create-user', 'Matt', '--admin'])
    assert result.exit_code == 0, result.output
    assert '24 hours' in result.output and 'two-factor' in result.output
    password = re.search(r'\b[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}\b', result.output).group()
    client = app.test_client()
    assert sign_in(client, 'matt', password).headers['Location'] == '/login/setup'
    assert set_up(client).status_code == 200
    assert client.get('/api/me').get_json()['role'] == 'admin'


def test_create_user_command_refuses_an_existing_or_bad_username(app):
    make_user('matt')
    result = app.test_cli_runner().invoke(args=['create-user', 'matt'])
    assert result.exit_code != 0 and 'already a user called matt' in result.output
    result = app.test_cli_runner().invoke(args=['create-user', 'not ok'])
    assert result.exit_code != 0 and 'username' in result.output


def test_reset_user_command(app, matt):
    signed_in = app.test_client()
    sign_in(signed_in, 'matt')
    result = app.test_cli_runner().invoke(args=['reset-user', 'Matt'])
    assert result.exit_code == 0, result.output
    assert '24 hours' in result.output
    password = re.search(r'\b[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}\b', result.output).group()
    assert signed_in.get('/api/me').status_code == 401
    assert sign_in(app.test_client(), 'matt').status_code == 401  # the old password no longer works
    client = app.test_client()
    assert sign_in(client, 'matt', password).headers['Location'] == '/login/setup'
    assert set_up(client).status_code == 200

    result = app.test_cli_runner().invoke(args=['reset-user', 'nobody'])
    assert result.exit_code != 0 and 'There is no user called nobody' in result.output


def test_no_secret_reaches_the_logs_on_a_first_sign_in(app, admin, caplog):
    caplog.set_level(logging.INFO)
    created = admin.post('/api/users', json={'username': 'sam', 'role': 'member'}).get_json()['one_time_password']
    client = app.test_client()
    sign_in(client, 'sam', created)
    secret = shown_secret(client.get('/login/setup'))
    set_up(client, password='too short')
    code = pyotp.TOTP(secret).now()
    response = set_up(client, code=code)
    codes = re.findall(r'<li>([0-9a-f-]{14})</li>', response.get_data(as_text=True))
    token = client.get_cookie('mcpanel_session').value
    reset = admin.post(f'/api/users/{user_row("sam")["id"]}/reset').get_json()['one_time_password']
    sign_in(client, 'sam', reset)
    printed = app.test_cli_runner().invoke(args=['reset-user', 'sam']).output
    logged = '\n'.join(str(vars(record)) for record in caplog.records)
    assert all(event in logged for event in ('user_created', 'setup_done', 'sign_in_reset'))
    for secret_text in (created, secret, code, NEW_PASSWORD, 'too short', *codes, token, accounts.hashed(token), reset):
        assert secret_text not in logged
    assert re.search(r'[0-9a-z]{4}(-[0-9a-z]{4}){3}', printed).group() not in logged
