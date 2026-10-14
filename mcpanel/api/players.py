"""A server's players, for anyone who may use the server. While it runs, changes go through the
console; while it's stopped, through its list files."""
from flask import Blueprint, abort, g

from .. import logs, players, rcon, servers
from .common import json_body, usable_server

bp = Blueprint('players', __name__)


@bp.get('/servers/<server_id>/players')
def list_players(server_id):
    """{online, max, all, whitelist, ops, banned}: who is on, who has ever joined, and the server's own lists."""
    server = usable_server(server_id)
    return players.players(server_id, server['status'] not in ('stopped', 'crashed'))


@bp.post('/servers/<server_id>/players')
def change_list(server_id):
    """{command, name, reason}: a whitelist, operator or ban change (command is the console's: whitelist
    add, op, pardon...), reason only for a ban -> {response}. While the server runs it is that console
    command; while it's stopped, the change is made in its list's file."""
    server = usable_server(server_id)
    data = json_body()
    command, name, reason = data.get('command'), data.get('name'), data.get('reason', '')
    if command not in players.CHANGES:
        abort(400, 'Expected whitelist add or remove, op, deop, ban or pardon')
    if not isinstance(name, str) or not players.NAME.fullmatch(name):
        abort(400, 'A player name is 1 to 16 letters, digits or _')
    if not isinstance(reason, str) or len(reason) > players.MAX_REASON or not reason.isprintable():
        abort(400, f'A reason is one line of at most {players.MAX_REASON} characters')
    reason = reason.strip() if command == 'ban' else ''
    if server['status'] in ('stopped', 'crashed'):
        response = players.change_saved(server_id, command, name, reason)
    else:
        response = rcon.run(server_id, servers.rcon_password(server_id), f'{command} {name} {reason}'.rstrip())
    if command == 'ban':
        players.note_ban(server_id, name, g.user['username'])
    logs.audit('player_list_changed', server=server['id'], server_name=server['name'], command=command, player=name, reason=reason or None)
    return {'response': response}
