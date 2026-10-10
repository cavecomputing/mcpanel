"""Backups of a server's folder: .tar.gz files in MCPANEL_DATA_DIR/backups/<server id>/, made, restored,
downloaded and deleted from the Backups tab. Nothing makes them on a schedule or deletes old ones.

A backup reads the folder the way files.py does, never following a link (links, FIFOs and the like
are left out), so a running server's code can't get the host's files into one. The .rcon-cli files
are left out and server.properties goes in with its RCON password masked: a backup can be downloaded.
A running server is told to save and stop writing the world (save-off) for as long as it takes.
A restore needs the server stopped, so nothing writes the folder while it is swapped for the backup.
"""
import io
import logging
import os
import re
import secrets
import shutil
import stat
import tarfile
import threading
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timezone

from flask import abort
from werkzeug.exceptions import HTTPException

from . import config, files, rcon, servers

logger = logging.getLogger(__name__)

NAME = re.compile(r'\d{4}-\d{2}-\d{2}_\d{6}\.tar\.gz')
LOCKS = defaultdict(threading.Lock)  # server id -> held while a backup or restore of it runs


def folder(server_id):
    return config.BACKUPS_DIR / servers.check_id(server_id)


def path_of(server_id, name):
    """A backup's file, or a 404."""
    if not isinstance(name, str) or not NAME.fullmatch(name) or not (folder(server_id) / name).is_file():
        abort(404, 'No such backup')
    return folder(server_id) / name


def backup_dict(path):
    info = path.stat()
    return {'name': path.name, 'size': info.st_size, 'created': int(info.st_mtime)}


def list_backups(server_id):
    """Every backup of a server, newest first."""
    if not folder(server_id).is_dir():
        return []
    return sorted((backup_dict(path) for path in folder(server_id).iterdir() if NAME.fullmatch(path.name)),
                  key=lambda backup: backup['name'], reverse=True)


def lock(server_id):
    """The server's backup lock, held, or a 409 while another backup or restore runs."""
    held = LOCKS[server_id]
    if not held.acquire(blocking=False):
        abort(409, 'A backup or restore of this server is already under way')
    return held


def console(server_id, command):
    """Run command on a running server, or log why it couldn't: a backup goes ahead either way."""
    try:
        rcon.run(server_id, servers.rcon_password(server_id), command)
        return True
    except HTTPException as e:
        logger.warning("Couldn't run %s on server %s before a backup: %s", command, server_id, e.description,
                       extra={'server': server_id})
        return False


def create(server_id, running):
    """Back up a server's folder and return the backup's dict."""
    held = lock(server_id)
    paused = running and console(server_id, 'save-off')
    try:
        if running:
            console(server_id, 'save-all flush')
        name = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S.tar.gz')
        target = folder(server_id) / name
        if target.exists():
            abort(409, 'A backup was made this second already')
        partial = folder(server_id) / f'.{name}.partial'
        with files.file_errors():
            folder(server_id).mkdir(parents=True, exist_ok=True)
            try:
                with tarfile.open(partial, 'w:gz', compresslevel=6) as tar, files.opened_dir(server_id, []) as fd:
                    add_folder(tar, fd, '')
                os.replace(partial, target)
            except BaseException:
                with suppress(FileNotFoundError):
                    partial.unlink()
                raise
    finally:
        if paused:
            console(server_id, 'save-on')
        held.release()
    logger.info('Backed up server %s', server_id, extra={'server': server_id, 'backup': name})
    return backup_dict(target)


def add_folder(tar, dir_fd, prefix):
    """Add what the folder dir_fd holds to tar under prefix: folders and regular files only, each
    opened without following a link."""
    for name in sorted(os.listdir(dir_fd)):
        if not prefix and name in files.HIDDEN:
            continue
        arcname = prefix + name
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                inner = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
                try:
                    entry = tarfile.TarInfo(arcname)
                    entry.type, entry.mode, entry.mtime = tarfile.DIRTYPE, stat.S_IMODE(info.st_mode), info.st_mtime
                    tar.addfile(entry)
                    add_folder(tar, inner, arcname + '/')
                finally:
                    os.close(inner)
            elif stat.S_ISREG(info.st_mode):
                with files.open_file(dir_fd, name) as file:
                    entry = tar.gettarinfo(arcname=arcname, fileobj=file)
                    if files.is_properties([], arcname):
                        data = files.masked_properties(file.read())
                        entry.size = len(data)
                        tar.addfile(entry, io.BytesIO(data))
                    else:
                        tar.addfile(entry, file)
        except FileNotFoundError:
            continue  # deleted since the listing


def restore(server_id, name):
    """Put a backup in place of a stopped server's folder. The folder it replaces is deleted."""
    source = path_of(server_id, name)
    held = lock(server_id)
    try:
        live = config.SERVERS_DIR / server_id
        token = secrets.token_hex(4)
        incoming, outgoing = config.SERVERS_DIR / f'.restore-{server_id}-{token}', config.SERVERS_DIR / f'.old-{server_id}-{token}'
        with files.file_errors():
            incoming.mkdir()
            try:
                try:
                    with tarfile.open(source) as tar:
                        tar.extractall(incoming, filter='data')  # nothing outside it, no links out, no devices
                except tarfile.TarError as e:
                    abort(422, f"That backup can't be restored: {e}")
                properties = incoming / files.PROPERTIES
                if properties.is_file() and not properties.is_symlink():
                    text = properties.read_text(errors='replace')
                    properties.write_text(files.RCON_PASSWORD.sub(
                        lambda match: match[1] + servers.rcon_password(server_id), text))
            except BaseException:
                shutil.rmtree(incoming, ignore_errors=True)
                raise
            os.rename(live, outgoing)
            os.rename(incoming, live)
            shutil.rmtree(outgoing, ignore_errors=True)
    finally:
        held.release()
    logger.info('Restored server %s from %s', server_id, name, extra={'server': server_id, 'backup': name})


def delete(server_id, name):
    path_of(server_id, name).unlink()
