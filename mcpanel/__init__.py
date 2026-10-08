"""mcpanel: a small web panel for hosting Minecraft servers in Docker."""
import mimetypes
import secrets
from urllib.parse import urlsplit

from flask import Flask, abort, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import api, auth, config, logs, views
from .db import init_db


def create_app():
    """The app. Refuses to start without MCPANEL_PUBLIC_HOST and a valid MCPANEL_PORT_RANGE."""
    if not config.PUBLIC_HOST:
        raise RuntimeError('Set MCPANEL_PUBLIC_HOST to the name players use to reach your servers.')
    config.port_range()  # raises with what is wrong with MCPANEL_PORT_RANGE

    for folder in (config.DATA_DIR, config.LOGS_DIR, config.SERVERS_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    logs.setup_logging()
    # Browsers refuse module scripts not served as JavaScript, and some systems map .js to text/plain.
    mimetypes.add_type('text/javascript', '.js')

    app = Flask(__name__)
    # Caddy is the one proxy in front: it says whether the browser used HTTPS (which decides whether
    # cookies are Secure) and which address the request came from (for the logs and the sign-in
    # throttle).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.session_interface = auth.SessionInterface()
    app.config.update(
        # Flask's own signed cookie only carries a sign-in halfway through (password done, code not
        # yet). Cookies ignore the port, so the names must not collide with sibling apps on the host.
        SESSION_COOKIE_NAME='mcpanel_signin',
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_HTTPONLY=True,
    )

    init_db()
    # Random for each process and never stored: with the key, a copy of the database (the TOTP
    # secrets) could sign that cookie and skip the password. A restart loses only sign-ins halfway.
    app.secret_key = secrets.token_bytes(32)

    app.before_request(logs.start_timer)
    app.before_request(reject_cross_site_writes)
    app.before_request(auth.require_login)
    app.after_request(logs.log_request)
    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(views.bp)
    app.cli.add_command(auth.invite_command)
    return app


def reject_cross_site_writes():
    """Refuse changes sent by another site's page in the same browser.

    Browsers send Sec-Fetch-Site over HTTPS and to localhost. Over plain HTTP elsewhere they send
    only Origin, which must then match the host and port exactly. Scripts like curl send neither.
    """
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    site = request.headers.get('Sec-Fetch-Site')
    origin = request.headers.get('Origin')
    if site in ('cross-site', 'same-site') \
            or (site is None and origin is not None and urlsplit(origin).netloc != request.host):
        abort(403, 'Cross-site request blocked')
