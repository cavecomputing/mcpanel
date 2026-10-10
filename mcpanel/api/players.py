"""A server's players, for anyone who may use the server. Changes go through the console."""
from flask import Blueprint

from .. import players
from .common import usable_server

bp = Blueprint('players', __name__)


@bp.get('/servers/<server_id>/players')
def list_players(server_id):
    """{online, max, whitelist, ops, banned}: who is on, and the server's own lists."""
    server = usable_server(server_id)
    return players.players(server_id, server['status'] not in ('stopped', 'crashed'))
