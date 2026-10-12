"""A server's players, for anyone who may use the server. While it runs, changes go through the
console; while it's stopped, through its list files."""
from flask import Blueprint, abort

from .. import logs, players
from .common import json_body, usable_server

bp = Blueprint('players', __name__)


@bp.get('/servers/<server_id>/players')
def list_players(server_id):
    """{online, max, whitelist, ops, banned}: who is on, and the server's own lists."""
    server = usable_server(server_id)
    return players.players(server_id, server['status'] not in ('stopped', 'crashed'))


@bp.post('/servers/<server_id>/players')
def change_list(server_id):
    """{command, name}: while the server is stopped, a whitelist, operator or ban change made in its
    list's file (command is the console's: whitelist add, op, pardon...) -> {response}."""
    server = usable_server(server_id)
    data = json_body()
    command, name = data.get('command'), data.get('name')
    if command not in players.CHANGES:
        abort(400, 'Expected whitelist add or remove, op, deop, ban or pardon')
    if not isinstance(name, str) or not players.NAME.fullmatch(name):
        abort(400, 'A player name is 1 to 16 letters, digits or _')
    if server['status'] not in ('stopped', 'crashed'):
        abort(409, 'It is running, so change this from its console')
    response = players.change_saved(server_id, command, name)
    logs.audit('player_list_changed', server=server['id'], server_name=server['name'], command=command, player=name)
    return {'response': response}
