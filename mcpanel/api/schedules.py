"""A server's schedules of restarts and backups, for anyone who may use the server, as Restart and
Back up now are: list, add, remove."""
from flask import Blueprint

from .. import logs, schedules
from .common import json_body, usable_server

bp = Blueprint('schedules', __name__, url_prefix='/servers/<server_id>/schedules')


@bp.get('')
def list_schedules(server_id):
    """{schedules: [{id, action, cron, timezone, next}]}, next being when it next comes (Unix seconds)."""
    usable_server(server_id)
    return {'schedules': schedules.for_server(server_id)}


@bp.post('')
def add(server_id):
    """{action, cron, timezone} -> the new schedule. action is restart or backup, timezone IANA's,
    e.g. America/Chicago."""
    server = usable_server(server_id)
    data = json_body()
    schedule = schedules.add(server_id, data.get('action'), data.get('cron'), data.get('timezone'))
    logs.audit('schedule_added', server=server['id'], server_name=server['name'], action=schedule['action'],
               cron=schedule['cron'], timezone=schedule['timezone'])
    return schedule, 201


@bp.delete('/<int:schedule_id>')
def remove(server_id, schedule_id):
    server = usable_server(server_id)
    schedule = schedules.remove(server_id, schedule_id)
    logs.audit('schedule_removed', server=server['id'], server_name=server['name'], action=schedule['action'],
               cron=schedule['cron'])
    return {}
