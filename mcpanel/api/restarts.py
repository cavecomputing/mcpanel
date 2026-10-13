"""A server's scheduled restarts, for anyone who may use the server, as Restart is: list, add, remove."""
from flask import Blueprint

from .. import logs, restarts
from .common import json_body, usable_server

bp = Blueprint('restarts', __name__, url_prefix='/servers/<server_id>/restarts')


@bp.get('')
def list_restarts(server_id):
    """{restarts: [{id, cron, timezone, next}]}, next being when it next happens (Unix seconds)."""
    usable_server(server_id)
    return {'restarts': restarts.for_server(server_id)}


@bp.post('')
def add(server_id):
    """{cron, timezone} -> the new scheduled restart. timezone is IANA's, e.g. America/Chicago."""
    server = usable_server(server_id)
    data = json_body()
    restart = restarts.add(server_id, data.get('cron'), data.get('timezone'))
    logs.audit('restart_scheduled', server=server['id'], server_name=server['name'], cron=restart['cron'],
               timezone=restart['timezone'])
    return restart, 201


@bp.delete('/<int:restart_id>')
def remove(server_id, restart_id):
    server = usable_server(server_id)
    line = restarts.remove(server_id, restart_id)
    logs.audit('restart_unscheduled', server=server['id'], server_name=server['name'], cron=line)
    return {}
