"""A server's console, for anyone who may use the server: its output, and commands over RCON."""
from flask import Blueprint, abort, request

from .. import logs, rcon, servers
from .common import json_body, usable_server

bp = Blueprint('console', __name__, url_prefix='/servers/<server_id>/console')


@bp.get('')
def output(server_id):
    """?since=<time> -> {lines, since}: the console's lines after since (the last 500 without it),
    and the time to ask from next."""
    usable_server(server_id)
    since = request.args.get('since', type=float)
    lines, last = servers.console_lines(server_id, since)
    return {'lines': lines, 'since': last}


@bp.post('')
def command(server_id):
    """{command} -> {response}: run a command as the console does, without the slash."""
    server = usable_server(server_id)
    text = json_body().get('command')
    text = text.strip().removeprefix('/') if isinstance(text, str) else ''
    if not text:
        abort(400, 'Type a command')
    if '\n' in text or '\r' in text or len(text.encode()) > rcon.MAX_COMMAND:
        abort(400, f'A command is one line of at most {rcon.MAX_COMMAND} bytes')
    if server['status'] in ('stopped', 'crashed'):
        abort(409, 'Start the server to send it commands')
    response = rcon.run(server_id, servers.rcon_password(server_id), text)
    logs.audit('server_command', server=server['id'], server_name=server['name'], command=text)
    return {'response': response}
