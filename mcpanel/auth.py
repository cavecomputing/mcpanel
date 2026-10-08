"""Signing in (a password, then a TOTP or recovery code, or on the first sign-in setting both up),
signing out, lockouts, the session cookie and the guard in front of every other route, and the
commands that make an account and reset one."""
import base64
import hmac
import math
import threading
import time

import click
import pyotp
import segno
from flask import (Blueprint, abort, current_app, g, jsonify, make_response, redirect, render_template, request,
                   session, url_for)
from flask.sessions import SecureCookieSessionInterface
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from . import accounts, logs
from .db import get_db

bp = Blueprint('auth', __name__)

COOKIE = 'mcpanel_session'
# Everything else needs a signed-in session, so a new route is protected without opting in.
OPEN_ENDPOINTS = {'auth.login', 'auth.login_code', 'auth.login_setup', 'auth.logout', 'views.healthz', 'static'}
MAX_TRIES = 5  # wrong passwords or codes in a row before the account locks
LOCK_FOR = 15 * 60
# From the right password to the code, or on a first sign-in to finishing the set-up, which may mean
# installing an authenticator app first.
PENDING_FOR = {'code': 5 * 60, 'setup': 15 * 60}

WRONG_PASSWORD = f'Wrong username or password. {MAX_TRIES} wrong tries in a row lock an account for {LOCK_FOR // 60} minutes.'
WRONG_CODE = "That code didn't work. Codes change every 30 seconds, so use the one showing now."
WRONG_RECOVERY_CODE = "That recovery code didn't work. Each one works once."
TOO_FAST = 'Too many wrong tries just now. Wait a moment and try again.'
EXPIRED = 'This one-time password has expired. Ask an admin for a new one.'
TOO_SLOW = 'That took too long. Sign in again.'
CHANGED = 'This account changed while you were signing in. Sign in again.'

# A wrong password or code holds off that address's next try for a second, the right one included,
# so one address guesses at one try a second however many requests it runs side by side. Keyed by
# address, so a stranger's wrong tries never hold off anyone else's sign-in; the account lock caps
# guessing at one account from many addresses. The cost: many addresses (an IPv6 /64), or a server
# container on the mcpanel network reaching port 5000 with a made-up X-Forwarded-For, can keep more
# request threads in the one-second sleep than one address could.
WRONG_TRY_WAIT = 1  # seconds
next_try = {}  # client address -> time.monotonic() before which its tries are refused unchecked
next_try_lock = threading.Lock()


class SessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):
        """Mark Flask's cookie (a sign-in halfway through) Secure whenever the browser used HTTPS."""
        return request.is_secure


def require_login():
    """before_request guard: g.user from the session cookie. Without one, API calls get a 401 and
    pages go to the sign-in page."""
    g.user = accounts.session_user(request.cookies.get(COOKIE))
    if g.user or request.endpoint in OPEN_ENDPOINTS:
        return None
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Sign in first'}), 401
    return redirect(url_for('auth.login', next=request.full_path.rstrip('?')))


def require_admin():
    """Abort with a 403 unless the signed-in user is an admin. Admin-only API routes call this first."""
    if g.user['role'] != 'admin':
        abort(403, 'Admins only')


def local_target(target):
    """target if it is a path on this site, else '/', so ?next= can't send the browser elsewhere.

    Browsers drop tabs and newlines from a URL and read a backslash as a slash, so any of them
    could turn it into "//elsewhere", another site.
    """
    if target and target.startswith('/') and not target.startswith('//') \
            and not any(c == '\\' or c <= ' ' for c in target):
        return target
    return '/'


def throttled(check):
    """check() -> an error message, or None when it let the person through; run one at a time.

    While this address's last wrong try is under WRONG_TRY_WAIT old, check isn't run and the answer
    is TOO_FAST.
    """
    global next_try
    address = request.remote_addr
    with next_try_lock:
        if time.monotonic() < next_try.get(address, 0):
            return TOO_FAST
        error = check()
        if error:
            now = time.monotonic()
            next_try = {other: until for other, until in next_try.items() if until > now}
            next_try[address] = now + WRONG_TRY_WAIT
    if error:
        time.sleep(WRONG_TRY_WAIT)  # so whoever mistyped can try again as soon as they see this
    return error


def lock_message(locked_until):
    """What the code step says while the account is locked; None when it isn't."""
    minutes = math.ceil((locked_until - time.time()) / 60)
    if minutes > 0:
        return f'Too many wrong tries locked this account. Try again in {minutes} minute{"" if minutes == 1 else "s"}.'
    return None


def failed_try(user, step):
    """Count a wrong password or code against user. The 5th in a row locks the account for 15
    minutes, and then this returns the lock message."""
    tries = user['failed_tries'] + 1
    locked_until = time.time() + LOCK_FOR if tries >= MAX_TRIES else user['locked_until']
    with get_db() as conn:
        conn.execute('UPDATE users SET failed_tries = ?, locked_until = ? WHERE id = ?',
                     (tries % MAX_TRIES, locked_until, user['id']))
        conn.commit()
    logs.audit('sign_in_failed', user_id=user['id'], username=user['username'], step=step, tries=tries)
    if tries >= MAX_TRIES:
        logs.audit('locked', user_id=user['id'], username=user['username'], minutes=LOCK_FOR // 60)
        return lock_message(locked_until)
    return None


def pending(step):
    """Whether this browser is halfway through signing in, at step ('code' or 'setup'), and in time."""
    return session.get('step') == step and time.time() - session['when'] <= PENDING_FOR[step]


def pending_user():
    """The account signing in halfway through, read afresh, or None when it has been removed or its
    password changed since the password step: a reset, a set-up finished in another tab."""
    user = accounts.user_by_id(session['user_id'])
    return user if user and user['password_changed'] == session['password_changed'] else None


def login_page(status=200, **context):
    """login.html: the password step, or the code step with step='code'."""
    with get_db() as conn:
        no_users = conn.execute('SELECT 1 FROM users LIMIT 1').fetchone() is None
    return render_template('login.html', no_users=no_users, **context), status


def signed_in(response, user_id, remember):
    """response, carrying the cookie of a new session for user_id."""
    token = accounts.new_session(user_id, remember, request.remote_addr, request.user_agent.string)
    response.set_cookie(COOKIE, token, max_age=accounts.REMEMBER_FOR if remember else None, path='/',
                        secure=request.is_secure, httponly=True, samesite='Lax')
    return response


@bp.route('/login', methods=['GET', 'POST'])
def login():
    """The first step: username and password. The right ones lead on to the code step, or for an
    account signing in with its one-time password, to the set-up."""
    next_path = local_target(request.args.get('next'))
    if g.user:
        return redirect(next_path)
    if request.method == 'GET':
        return login_page(next=next_path, remember=True)
    username, remember = request.form.get('username', ''), request.form.get('remember') == 'on'
    user = None

    def check():
        nonlocal user
        user = accounts.user_named(username)
        # A locked account checks the dummy hash: every password is wrong and takes as long, so the
        # lock stops guessing rather than telling the guesser when they got the password right.
        locked = user and lock_message(user['locked_until'])
        if not accounts.password_matches(None if locked else user, request.form.get('password', '')):
            if user:
                failed_try(user, 'password')  # even the try that locks it gets the plain message
            else:
                logs.audit('sign_in_failed', step='password', known_user=False)
            return WRONG_PASSWORD
        if user['totp_secret'] is None and time.time() > user['password_changed'] + accounts.ONE_TIME_FOR:
            # Said only now, like the lock message: the password was right. Not counted toward the lock.
            logs.audit('sign_in_failed', user_id=user['id'], username=user['username'], step='expired one-time password')
            return EXPIRED
        return None

    error = throttled(check)
    if error:
        return login_page(429 if error == TOO_FAST else 401, next=next_path, error=error, username=username, remember=remember)
    # Flask's signed cookie carries the sign-in to the code step, or for an account without TOTP yet
    # (signed in with its one-time password) to the set-up, and nothing else ever goes in it.
    step = 'setup' if user['totp_secret'] is None else 'code'
    session.clear()
    session.update(user_id=user['id'], password_changed=user['password_changed'], step=step, remember=remember,
                   when=time.time(), next=next_path)
    return redirect(url_for(f'auth.login_{step}'))


@bp.route('/login/code', methods=['GET', 'POST'])
def login_code():
    """The second step: the 6-digit code from the authenticator app, or with ?recovery=1 a recovery code."""
    recovery = request.args.get('recovery') == '1'
    if not pending('code'):
        session.clear()
        if request.method == 'GET':
            return redirect(url_for('auth.login'))
        return login_page(401, error=TOO_SLOW, remember=True)
    if request.method == 'GET':
        return login_page(step='code', recovery=recovery)
    code = request.form.get('code', '')
    user = None

    def check():
        nonlocal user
        user = pending_user()
        if user is None:
            return CHANGED
        error = lock_message(user['locked_until'])
        if error:
            return error
        if accounts.use_recovery_code(user['id'], code) if recovery else accounts.code_matches(user, code):
            return None
        return failed_try(user, 'recovery code' if recovery else 'code') or (WRONG_RECOVERY_CODE if recovery else WRONG_CODE)

    error = throttled(check)
    if error:
        return login_page(429 if error == TOO_FAST else 401, step='code', recovery=recovery, error=error)
    with get_db() as conn:
        conn.execute('UPDATE users SET failed_tries = 0 WHERE id = ?', (user['id'],))
        conn.commit()
    remember, next_path = session['remember'], session['next']
    session.clear()
    logs.audit('sign_in', user_id=user['id'], username=user['username'], remember=remember,
               factor='recovery code' if recovery else 'totp')
    return signed_in(redirect(next_path), user['id'], remember)


@bp.post('/logout')
def logout():
    token = request.cookies.get(COOKIE)
    if token:
        accounts.end_session(token)
    if g.user:
        logs.audit('sign_out', user_id=g.user['id'], username=g.user['username'])
    session.clear()
    response = redirect(url_for('auth.login'))
    response.delete_cookie(COOKIE, path='/', secure=request.is_secure, httponly=True, samesite='Lax')
    return response


def setup_secret():
    """The TOTP secret the set-up adds to the authenticator app, for the account signing in halfway
    through: made from the process's random key and the one-time password's time, never stored
    until the set-up saves it. A reload shows the same QR code; a restart or a reset, another."""
    message = f'totp setup {session["user_id"]} {session["password_changed"]!r}'.encode()
    return base64.b32encode(hmac.digest(current_app.secret_key, message, 'sha256')[:20]).decode()


def setup_page(user, secret, error=None):
    """setup.html's form: a new password twice, the QR code and key for secret, a code. Not cached,
    since it shows the secret."""
    uri = pyotp.TOTP(secret).provisioning_uri(name=user['username'], issuer_name='mcpanel')
    qr = Markup(segno.make(uri).svg_inline(scale=4))
    response = make_response(render_template('setup.html', username=user['username'], secret=secret, qr=qr, error=error),
                             429 if error == TOO_FAST else 400 if error else 200)
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.route('/login/setup', methods=['GET', 'POST'])
def login_setup():
    """The first sign-in's second step, after the one-time password: choose a password and add TOTP to
    an authenticator app. Done, the account is signed in and shown its recovery codes, once."""
    user = pending('setup') and pending_user()
    if not user:
        error = CHANGED if pending('setup') else TOO_SLOW
        session.clear()
        if request.method == 'GET':
            return redirect(url_for('auth.login'))
        return login_page(401, error=error, remember=True)
    secret = setup_secret()
    if request.method == 'GET':
        return setup_page(user, secret)
    password = request.form.get('password', '')

    def check():
        if accounts.password_matches(user, password):
            return 'Choose a new password, not the one-time one.'
        step = accounts.totp_step(secret, request.form.get('code', ''), 0)
        if step is None:
            logs.audit('sign_in_failed', user_id=user['id'], username=user['username'], step='setup')
            return WRONG_CODE
        return None if accounts.finish_setup(user, password, secret, step) else CHANGED

    if len(password) < accounts.MIN_PASSWORD:
        error = f'Choose a password of at least {accounts.MIN_PASSWORD} characters.'
    elif password != request.form.get('again'):
        error = "The two passwords aren't the same."
    else:
        error = throttled(check)
    if error:
        return setup_page(user, secret, error)
    codes = accounts.new_recovery_codes(user['id'])
    remember, next_path = session['remember'], session['next']
    session.clear()
    logs.audit('setup_done', user_id=user['id'], username=user['username'], remember=remember)
    response = make_response(render_template('setup.html', username=user['username'], codes=codes, next=next_path))
    response.headers['Cache-Control'] = 'no-store'
    return signed_in(response, user['id'], remember)


def one_time_note(username, password):
    """What the commands print: the one-time password and what to do with it."""
    return (f'The one-time password for {username}, which works for {accounts.ONE_TIME_FOR // 3600} hours:\n\n'
            f'    {password}\n\n'
            'They sign in with it, then choose their own password and set up two-factor sign-in.')


@click.command('create-user')
@click.argument('username')
@click.option('--admin', is_flag=True, help='Make an admin rather than a member.')
def create_user_command(username, admin):
    """Make an account called USERNAME and print its one-time password.

    A member starts with no servers; an admin gives them some on the Users page.
    """
    role = 'admin' if admin else 'member'
    try:
        username = accounts.check_username(username)
        user_id, password = accounts.create_user(username, role, [])
    except HTTPException as e:
        raise click.ClickException(e.description)
    logs.audit('user_created', user_id=user_id, username=username, role=role, servers=[], by='command line')
    click.echo(one_time_note(username, password))


@click.command('reset-user')
@click.argument('username')
def reset_user_command(username):
    """Start USERNAME's sign-in over and print their new one-time password.

    Their password, two-factor set-up and recovery codes stop working, every device of theirs is
    signed out, and a lock is lifted. For a lost phone, or when every admin is locked out.
    """
    user = accounts.user_named(username)
    if user is None:
        raise click.ClickException(f'There is no user called {username}')
    password = accounts.reset_sign_in(user['id'])
    logs.audit('sign_in_reset', user_id=user['id'], username=user['username'], by='command line')
    click.echo(one_time_note(user['username'], password))
