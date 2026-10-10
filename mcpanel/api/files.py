"""A server's files, for anyone who may use the server: list, open, save, upload, download, new
folder, rename, delete. files.py keeps every path inside the server's folder."""
from flask import Blueprint, abort, request, send_file

from .. import files, logs, servers
from .common import json_body, usable_server

bp = Blueprint('files', __name__, url_prefix='/servers/<server_id>/files')


def path_arg():
    return request.args.get('path', '')


def audit(event, server, **fields):
    logs.audit(event, server=server['id'], server_name=server['name'], **fields)


@bp.get('')
def list_dir(server_id):
    """?path=folder -> {path, entries: [{name, type, size, modified}]}; type is dir, file, link or other."""
    usable_server(server_id)
    return {'path': '/'.join(files.parts_of(path_arg())), 'entries': files.list_dir(server_id, path_arg())}


@bp.get('/content')
def read(server_id):
    """?path=file -> {text}, for the editor: UTF-8 text up to 1 MB."""
    usable_server(server_id)
    return {'text': files.read_text(server_id, path_arg())}


@bp.put('/content')
def write(server_id):
    """?path=file, the body is the file: saves from the editor and uploads alike, in full or not at all."""
    server = usable_server(server_id)
    if (request.content_length or 0) > files.MAX_UPLOAD:
        abort(413, f'Files can be at most {files.MAX_UPLOAD // 1024 ** 3} GB')
    files.write(server_id, path_arg(), iter(lambda: request.stream.read(files.CHUNK), b''),
                servers.rcon_password(server_id))
    audit('file_written', server, path=path_arg(), size=request.content_length)
    return {}


@bp.get('/download')
def download(server_id):
    """?path=file, as an attachment. Never shown inline: a page in a server's folder must not run as the panel."""
    usable_server(server_id)
    file, name = files.download(server_id, path_arg())
    response = send_file(file, mimetype='application/octet-stream', as_attachment=True, download_name=name)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.post('/folder')
def make_folder(server_id):
    """{path} of a new folder, whose parent is there already."""
    server = usable_server(server_id)
    path = json_body().get('path')
    files.make_folder(server_id, path)
    audit('folder_created', server, path=path)
    return {}, 201


@bp.post('/rename')
def rename(server_id):
    """{path, to}: rename or move a file or folder within the server's folder."""
    server = usable_server(server_id)
    data = json_body()
    files.rename(server_id, data.get('path'), data.get('to'))
    audit('file_renamed', server, path=data['path'], to=data['to'])
    return {}


@bp.delete('')
def delete(server_id):
    """?path=: a file, or a folder with everything in it."""
    server = usable_server(server_id)
    files.delete(server_id, path_arg())
    audit('file_deleted', server, path=path_arg())
    return {}
