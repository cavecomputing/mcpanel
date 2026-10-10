"""A server's backups, for anyone who may use the server: list, make, download, restore, delete."""
from flask import Blueprint, abort, send_file

from .. import backups, logs
from .common import usable_server

bp = Blueprint('backups', __name__, url_prefix='/servers/<server_id>/backups')


def audit(event, server, name):
    logs.audit(event, server=server['id'], server_name=server['name'], backup=name)


@bp.get('')
def list_backups(server_id):
    usable_server(server_id)
    return {'backups': backups.list_backups(server_id)}


@bp.post('')
def create(server_id):
    """Back the server's folder up now, running or not; the new backup's dict."""
    server = usable_server(server_id)
    backup = backups.create(server_id, server['status'] not in ('stopped', 'crashed'))
    audit('backup_created', server, backup['name'])
    return backup, 201


@bp.get('/<name>')
def download(server_id, name):
    usable_server(server_id)
    return send_file(backups.path_of(server_id, name), mimetype='application/gzip', as_attachment=True,
                     download_name=f'{server_id}-{name}')


@bp.post('/<name>/restore')
def restore(server_id, name):
    """Replace the stopped server's folder with a backup."""
    server = usable_server(server_id)
    if server['status'] not in ('stopped', 'crashed'):
        abort(409, 'Stop the server before restoring a backup')
    backups.restore(server_id, name)
    audit('backup_restored', server, name)
    return {}


@bp.delete('/<name>')
def delete(server_id, name):
    server = usable_server(server_id)
    backups.delete(server_id, name)
    audit('backup_deleted', server, name)
    return {}
