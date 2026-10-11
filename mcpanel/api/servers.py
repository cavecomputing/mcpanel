"""Servers: list, create, change, delete, start, stop, restart. Members see and run only the servers
an admin gave them, and change everything on them but their memory and CPU."""
from flask import Blueprint, abort, g

from .. import accounts, auth, logs, players, properties, servers
from ..db import get_db
from .common import json_body, usable_server

bp = Blueprint('servers', __name__)

DEFAULT_MEMORY_GB = 4

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
    """The choices the new-server form and the Settings tab offer."""
    return {'types': list(servers.TYPES), 'java': list(servers.JAVA_TAGS),
            'memory': {'min': 1, 'max': servers.MAX_MEMORY_GB, 'default': DEFAULT_MEMORY_GB},
            'cpus': {'min': servers.MIN_CPUS}}


@bp.get('/servers/host')
def host():
    """The host's memory and cores, which the forms that allocate them show. Admins only, as those are."""
    auth.require_admin()
    return servers.host()


@bp.post('/servers')
def create():
    """Create a server, stopped: {name, type, version, java, memory_gb, cpus, eula}. create_server() checks them."""
    auth.require_admin()
    data = json_body()
    server = servers.create_server(data.get('name'), data.get('type'), data.get('version'), data.get('java'),
                                   data.get('memory_gb'), data.get('cpus'), data.get('eula'))
    logs.audit('server_created', server=server['id'], server_name=server['name'])
    return server, 201


@bp.put('/servers/<server_id>')
def update(server_id):
    """Change a stopped server: {name, type, version, java, memory_gb, cpus}; its fresh dict. Only
    admins change its memory and CPU, which are the container's resource allocation."""
    server = usable_server(server_id)
    data = json_body()
    if data.get('memory_gb') != server['memory_gb'] and g.user['role'] == 'member':
        abort(403, "Only admins change a server's memory")
    if data.get('cpus') != server['cpus'] and g.user['role'] == 'member':
        abort(403, "Only admins change a server's CPU")
    server = servers.update_server(server_id, data.get('name'), data.get('type'), data.get('version'),
                                   data.get('java'), data.get('memory_gb'), data.get('cpus'))
    logs.audit('server_changed', server=server['id'], server_name=server['name'], type=server['type'],
               version=server['version'], java=server['java'], memory_gb=server['memory_gb'], cpus=server['cpus'])
    return server


@bp.delete('/servers/<server_id>')
def delete(server_id):
    """Delete a stopped server, its files with it. Admins only, as creating one is."""
    auth.require_admin()
    server = usable_server(server_id)
    servers.delete_server(server_id)
    with get_db() as conn:  # so a server made later with the same id isn't given to them
        conn.execute('DELETE FROM server_access WHERE server = ?', (server_id,))
        conn.commit()
    logs.audit('server_deleted', server=server['id'], server_name=server['name'])
    return {}


@bp.get('/servers/<server_id>/stats')
def stats(server_id):
    """{usage, players, max_players} for the Overview: usage is servers.usage(), and players who
    is online. Each is null when the server isn't running or can't say yet."""
    server = usable_server(server_id)
    running = server['status'] not in ('stopped', 'crashed')
    names, most = (players.online(server_id) if running else None) or (None, None)
    return {'usage': servers.usage(server_id) if running else None,
            'players': None if names is None else len(names), 'max_players': most}


@bp.get('/servers/<server_id>/properties')
def get_properties(server_id):
    """The game settings in its server.properties; pvp from its game rule from 1.21.9, null while it
    can't say."""
    return properties.read(usable_server(server_id))


@bp.put('/servers/<server_id>/properties')
def put_properties(server_id):
    """Change some game settings: {key: value}; all of them after."""
    server = usable_server(server_id)
    changes = json_body()
    values = properties.save(server, changes)
    logs.audit('properties_changed', server=server['id'], server_name=server['name'], changes=changes)
    return values


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
