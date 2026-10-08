"""Logging, with fixed defaults and no settings.

Every line goes to stdout as one JSON object (readable plain lines when stdout is a terminal) and,
as JSON, to data/logs/mcpanel.log. Audit lines also go to data/logs/audit.log. Both files rotate at
10 MB and keep 7 gzipped. Every handler masks secrets on the way out.
"""
import copy
import gzip
import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from flask import g, has_request_context, request

from . import config

MAX_BYTES = 10 * 1024 * 1024  # a log file's size before it rotates
BACKUP_COUNT = 7  # rotated files kept, gzipped

# The names a LogRecord already uses, plus a line's own fields. Anything else on a record came from
# extra=; audit() renames fields that would clash, because LogRecord refuses them.
TAKEN = set(vars(logging.makeLogRecord({}))) | {'message', 'asctime', 'ts', 'level', 'logger', 'exc'}

# A field is secret when a part of its name says so: rcon_password and session_token are,
# exit_code and status_code are not.
SECRET_NAME = re.compile(
    r'(?:^|[_-])(?:password|passwd|secret|token|totp|otp|recovery|cookie|rcon|authorization|api[_-]key)s?(?:[_-]|$)',
    re.IGNORECASE)

installed_handlers = []  # (logger, handler) pairs from the last setup_logging()


def setup_logging():
    """Log to stdout, and to mcpanel.log and audit.log in config.LOGS_DIR.

    Calling it again closes and replaces the handlers it installed before (the tests build an app per
    test, each with its own LOGS_DIR) and leaves alone handlers it didn't install, like pytest's.
    """
    for logger, handler in installed_handlers:
        logger.removeHandler(handler)
        handler.close()
    installed_handlers.clear()

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stdout = logging.StreamHandler(sys.stdout)  # looked up now, so pytest's capture sees the lines
    stdout.setFormatter(PlainFormatter() if sys.stdout.isatty() else JsonFormatter())
    root = logging.getLogger()
    for logger, handler in ((root, stdout), (root, log_file('mcpanel.log')),
                            (logging.getLogger('mcpanel.audit'), log_file('audit.log'))):
        handler.addFilter(mask_secrets)
        logger.addHandler(handler)
        installed_handlers.append((logger, handler))
    root.setLevel(logging.INFO)
    logging.getLogger('werkzeug').addFilter(not_request_line)  # the same function each time, so added once


def not_request_line(record):
    """Logger filter: drop the dev server's request lines, which would repeat ours with query strings
    in them, and keep the rest of what it says (its address, reloads, the debugger PIN)."""
    return not record.msg.endswith('"%s" %s %s')


def log_file(name):
    """A JSON-lines handler for config.LOGS_DIR / name, rotated and gzipped as in the logging cookbook."""
    handler = RotatingFileHandler(config.LOGS_DIR / name, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                  encoding='utf-8')
    handler.namer = lambda path: path + '.gz'
    handler.rotator = gzip_file
    handler.setFormatter(JsonFormatter())
    return handler


def gzip_file(source, dest):
    with open(source, 'rb') as plain, gzip.open(dest, 'wb') as packed:
        shutil.copyfileobj(plain, packed)
    os.remove(source)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, msg, the fields passed with extra=, then exc."""

    def format(self, record):
        line = {
            'ts': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds')
                  .replace('+00:00', 'Z'),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
            **extra_fields(record),
        }
        if record.exc_info:
            line['exc'] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


class PlainFormatter(logging.Formatter):
    """For a person at a terminal: 2026-10-08 10:00:00 INFO mcpanel.auth: message key=value."""

    def __init__(self):
        super().__init__('%(asctime)s %(levelname)s %(name)s: %(message)s', '%Y-%m-%d %H:%M:%S')

    def formatMessage(self, record):
        return super().formatMessage(record) + ''.join(f' {k}={v}' for k, v in extra_fields(record).items())


def extra_fields(record):
    return {key: value for key, value in vars(record).items() if key not in TAKEN}


def mask_secrets(record):
    """Handler filter: a copy of record with secret-named fields masked.

    A backstop, not permission to log secrets. The original record goes on unchanged to handlers
    this module didn't install.
    """
    record = copy.copy(record)
    for key, value in extra_fields(record).items():
        setattr(record, key, masked(key, value))
    return record


def masked(key, value):
    """value, or *** when key names a secret. Dicts are checked key by key."""
    if SECRET_NAME.search(str(key)):
        return '***'
    if isinstance(value, dict):
        return {k: masked(k, v) for k, v in value.items()}
    return value


def audit(event, **fields):
    """Record who did what: in audit.log, and like any other line on stdout and in mcpanel.log.

    Inside a request, user (whoever is signed in) and ip are filled in and win over fields of the
    same name, so name the account acted on with user_id= or username=.
    """
    line = {'event': event, **fields}
    if has_request_context():
        if g.get('user'):
            line['user'] = g.user['username']
        line['ip'] = request.remote_addr
    extra = {f'field_{key}' if key in TAKEN else key: value for key, value in line.items()}
    logging.getLogger('mcpanel.audit').info(event, extra=extra)


def start_timer():
    """before_request, registered ahead of the guards so their answers are timed too."""
    g.request_started = time.perf_counter()


def log_request(response):
    """after_request: one line per request, except health checks and static files."""
    if request.path == '/healthz' or request.path.startswith('/static/'):
        return response
    started = g.get('request_started', time.perf_counter())
    fields = {'method': request.method, 'path': request.path, 'status': response.status_code,
              'ms': round((time.perf_counter() - started) * 1000), 'ip': request.remote_addr}
    if g.get('user'):
        fields['user'] = g.user['username']
    logging.getLogger('mcpanel.request').info('%s %s %s', request.method, request.path, response.status_code, extra=fields)
    return response
