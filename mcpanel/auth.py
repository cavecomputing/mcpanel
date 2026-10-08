"""Signing in (a password, then a TOTP or recovery code), signing out, invite links, lockouts, the
session cookie and the guard in front of every other route."""
import math
import threading
import time

import click
import pyotp
import segno
from flask import Blueprint, abort, g, jsonify, make_response, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from markupsafe import Markup
from werkzeug.exceptions import HTTPException

from . import accounts, logs
from .db import get_db

bp = Blueprint('auth', __name__)

COOKIE = 'mcpanel_session'
# Everything else needs a signed-in session, so a new route is protected without opting in.
OPEN_ENDPOINTS = {'auth.login', 'auth.login_code', 'auth.logout', 'auth.invite', 'views.healthz', 'static'}
MAX_TRIES = 5  # wrong passwords or codes in a row before the account locks
LOCK_FOR = 15 * 60
PENDING_FOR = 5 * 60  # from the right password to the code

WRONG_PASSWORD = 'Wrong username or password.'
WRONG_CODE = "That code didn't work. Codes change every 30 seconds, so use the one showing now."
WRONG_RECOVERY_CODE = "That recovery code didn't work. Each one works once."
TOO_FAST = 'Too many wrong tries just now. Wait a moment and try again.'

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
    """What someone who got the password right is told while the account is locked; None when it isn't."""
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
    """The first step: username and password. The right ones lead on to the code step."""
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
        if not accounts.password_matches(user, request.form.get('password', '')):
            if user:
                failed_try(user, 'password')  # even the try that locks it gets the plain message
            else:
                logs.audit('sign_in_failed', step='password', known_user=False)
            return WRONG_PASSWORD
        error = lock_message(user['locked_until'])
        if error:
            logs.audit('sign_in_failed', user_id=user['id'], username=user['username'], step='password', locked=True)
        return error

    error = throttled(check)
    if error:
        return login_page(429 if error == TOO_FAST else 401, next=next_path, error=error, username=username, remember=remember)
    # Flask's signed cookie carries the sign-in to the code step, and nothing else ever goes in it.
    session.clear()
    session.update(user_id=user['id'], remember=remember, when=time.time(), next=next_path)
    return redirect(url_for('auth.login_code'))


@bp.route('/login/code', methods=['GET', 'POST'])
def login_code():
    """The second step: the 6-digit code from the authenticator app, or with ?recovery=1 a recovery code."""
    recovery = request.args.get('recovery') == '1'
    if 'user_id' not in session or time.time() - session['when'] > PENDING_FOR:
        session.clear()
        if request.method == 'GET':
            return redirect(url_for('auth.login'))
        return login_page(401, error='That took too long. Sign in again.', remember=True)
    if request.method == 'GET':
        return login_page(step='code', recovery=recovery)
    code = request.form.get('code', '')
    user = None

    def check():
        nonlocal user
        user = accounts.user_by_id(session['user_id'])
        if user is None:
            return 'That account no longer exists.'
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


def invite_page(invite, error=None):
    """The set-up card: a password twice, the QR code and key for the account's TOTP secret, a code."""
    uri = pyotp.TOTP(invite['totp_secret']).provisioning_uri(name=invite['username'], issuer_name='mcpanel')
    qr = Markup(segno.make(uri).svg_inline(scale=4))
    return render_template('invite.html', invite=invite, qr=qr, error=error), 400 if error else 200


@bp.route('/invite/<token>', methods=['GET', 'POST'])
def invite(token):
    """Set up an invited account. Done, it is signed in and shown its recovery codes, once."""
    invite = accounts.invite_for(token)
    if invite is None:
        return render_template('invite.html'), 404
    if request.method == 'GET':
        return invite_page(invite)
    password = request.form.get('password', '')
    user_id = None

    def check():
        nonlocal user_id
        step = accounts.totp_step(invite['totp_secret'], request.form.get('code', ''), 0)
        if step is None:
            return WRONG_CODE
        user_id = accounts.accept_invite(invite, password, step)
        return None

    if len(password) < accounts.MIN_PASSWORD:
        error = f'Choose a password of at least {accounts.MIN_PASSWORD} characters.'
    elif password != request.form.get('again'):
        error = "The two passwords aren't the same."
    else:
        error = throttled(check)
    if error:
        return invite_page(invite, error)
    if user_id is None:  # used meanwhile, from another tab
        return render_template('invite.html'), 404
    codes = accounts.new_recovery_codes(user_id)
    session.clear()
    logs.audit('invite_used', user_id=user_id, username=invite['username'], role=invite['role'])
    response = make_response(render_template('invite.html', username=invite['username'], codes=codes))
    response.headers['Cache-Control'] = 'no-store'
    return signed_in(response, user_id, remember=False)


@click.command('invite')
@click.argument('username')
@click.option('--admin', is_flag=True, help='Make an admin rather than a member.')
def invite_command(username, admin):
    """Print a one-time link that sets up an account called USERNAME."""
    role = 'admin' if admin else 'member'
    try:
        username = accounts.check_username(username)
        token = accounts.create_invite(username, role, [], None)
    except HTTPException as e:
        raise click.ClickException(e.description)
    logs.audit('invite_created', username=username, role=role, by='command line')
    click.echo(f'Invite for {username} ({role}). It works once, for 24 hours. Open it on the panel\'s\n'
               f'address, after the https://... you reach mcpanel at in the browser:\n\n    /invite/{token}')
