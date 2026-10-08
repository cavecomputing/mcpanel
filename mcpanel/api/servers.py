"""Servers: list, create, start, stop, restart. Members see and run only the servers an admin gave them."""
from flask import Blueprint, abort, g

from .. import accounts, auth, logs, servers
from .common import json_body

bp = Blueprint('servers', __name__)

DEFAULT_HEAP_GB = 4

# action in the URL -> what does it, and the audit event it records
ACTIONS = {
    'start': (servers.start_server, 'server_started'),
    'stop': (servers.stop_server, 'server_stopped'),
    'restart': (servers.restart_server, 'server_restarted'),
}


@bp.get('/servers')
def list_servers():
    """The servers the signed-in user may use, by name. Granted ids Docker no longer has are left out."""
    allowed = accounts.servers_for(g.user)
    return {'servers': [server for server in servers.list_servers() if allowed is None or server['id'] in allowed]}


@bp.get('/servers/options')
def options():
    """The choices the new-server form offers."""
    auth.require_admin()
    return {'types': list(servers.TYPES), 'java': list(servers.JAVA_TAGS),
            'heap': {'min': 1, 'max': servers.MAX_HEAP_GB, 'default': DEFAULT_HEAP_GB}}


@bp.post('/servers')
def create():
    """Create and start a server: {name, type, version, java, heap_gb, eula}. create_server() checks them."""
    auth.require_admin()
    data = json_body()
    server = servers.create_server(data.get('name'), data.get('type'), data.get('version'), data.get('java'),
                                   data.get('heap_gb'), data.get('eula'))
    logs.audit('server_created', server=server['id'], server_name=server['name'])
    return server, 201


@bp.post('/servers/<server_id>/<any(start, stop, restart):action>')
def change(server_id, action):
    """Start, stop or restart a server; its fresh dict."""
    # A server the user may not use gets the same 404 as a missing one, so ids can't be probed.
    if not accounts.may_use(g.user, server_id):
        abort(404, 'No such server')
    act, event = ACTIONS[action]
    server = act(server_id)
    logs.audit(event, server=server['id'], server_name=server['name'])
    return server
