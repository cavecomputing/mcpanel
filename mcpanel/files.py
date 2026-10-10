"""A server's files: its folder under MCPANEL_DATA_DIR/servers, which is the container's /data.

The server's own code (a plugin, a mod) can write anything in that folder while the panel reads it,
symlinks to the host's files included. So nothing here follows a link or trusts a path: every path
is walked one folder at a time from the server's folder, each step opened with O_NOFOLLOW relative
to the one before (opened_dir()), and only regular files are read. A link shows in a listing and
can be renamed or deleted, never opened.

The image writes the server's RCON password into the folder: the .rcon-cli files are left out of
everything, and server.properties is handed out with it masked (masked_properties()).
"""
import errno
import io
import os
import re
import secrets
import shutil
import stat
from contextlib import contextmanager, suppress

from flask import abort

from . import config

# The image's copies of the RCON password, for its own rcon-cli. Never listed, read or written.
HIDDEN = {'.rcon-cli.env', '.rcon-cli.yaml'}
PROPERTIES = 'server.properties'
RCON_PASSWORD = re.compile(r'^(rcon\.password\s*[=:]).*$', re.MULTILINE)
MASK = '(hidden by mcpanel)'
MAX_EDIT = 1024 ** 2         # bytes the editor opens; bigger files are downloaded instead
MAX_UPLOAD = 1024 ** 3       # bytes in one upload or save
CHUNK = 1024 ** 2


def parts_of(path):
    """A path in a server's folder ('world/region', '' for the folder itself) as its names, or a 400.
    Never '.' or '..', so a path can only go down."""
    if not isinstance(path, str):
        abort(400, 'Expected a path')
    parts = [part for part in path.split('/') if part]
    if any(part in ('.', '..') or '\0' in part for part in parts):
        abort(400, "Paths can't contain . or ..")
    if parts and parts[0] in HIDDEN:
        abort(404, 'No such file or folder')
    return parts


@contextmanager
def file_errors():
    """Turn the OSErrors of file work into abort() with what the UI says."""
    try:
        yield
    except FileNotFoundError:
        abort(404, 'No such file or folder')
    except FileExistsError:
        abort(409, 'Something with that name is already there')
    except IsADirectoryError:
        abort(400, "That's a folder")
    except NotADirectoryError:
        abort(400, "That's not a folder")
    except PermissionError:
        abort(403, "The panel isn't allowed to change that")
    except OSError as e:
        if e.errno == errno.ELOOP:  # a link, which is never followed
            abort(400, "That's a link, which the panel doesn't follow")
        if e.errno == errno.ENOSPC:
            abort(507, 'The disk is full')
        if e.errno == errno.EINVAL:  # a folder moved into itself
            abort(400, "A folder can't go inside itself")
        abort(500, e.strerror or str(e))


@contextmanager
def opened_dir(server_id, parts):
    """A file descriptor of the folder parts names in server_id's folder, opened one step at a time
    without following links, so nothing the server writes can lead the panel out of its folder."""
    fd = os.open(config.SERVERS_DIR / server_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts:
            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = inner
        yield fd
    finally:
        os.close(fd)


def open_file(dir_fd, name):
    """A regular file in dir_fd, opened to read, never through a link. O_NONBLOCK so a FIFO the
    server made can't hang the panel; it's refused like any file that isn't a regular one."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        abort(400, "That's not a file the panel can open")
    return os.fdopen(fd, 'rb')


def split(path):
    """(the folder's names, the name in it) of a path to something, never the server's folder itself."""
    parts = parts_of(path)
    if not parts:
        abort(400, 'Name a file or folder')
    return parts[:-1], parts[-1]


def entry_type(mode):
    return 'dir' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else 'link' if stat.S_ISLNK(mode) else 'other'


def list_dir(server_id, path):
    """What a folder holds, folders first then by name: [{name, type, size, modified}]."""
    parts = parts_of(path)
    entries = []
    with file_errors(), opened_dir(server_id, parts) as fd:
        for name in os.listdir(fd):
            if not parts and name in HIDDEN:
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # deleted since the listing
            entries.append({'name': name, 'type': entry_type(info.st_mode),
                            'size': info.st_size if stat.S_ISREG(info.st_mode) else None, 'modified': int(info.st_mtime)})
    entries.sort(key=lambda entry: (entry['type'] != 'dir', entry['name'].casefold(), entry['name']))
    return entries


def masked_properties(data):
    """server.properties' bytes with the RCON password masked."""
    return RCON_PASSWORD.sub(lambda match: match[1] + MASK, data.decode('utf-8', 'replace')).encode()


def is_properties(parts, name):
    return not parts and name == PROPERTIES


def read_bytes(server_id, path, limit=None):
    """A file's bytes, server.properties with its RCON password masked. Over limit is a 413."""
    parts, name = split(path)
    with file_errors(), opened_dir(server_id, parts) as fd, open_file(fd, name) as file:
        if limit is not None and os.fstat(file.fileno()).st_size > limit:
            abort(413, "That file is too big to open here; download it instead")
        data = file.read()
    return masked_properties(data) if is_properties(parts, name) else data


def read_text(server_id, path):
    """A file's text for the editor, or a 400 when it isn't UTF-8 text."""
    data = read_bytes(server_id, path, MAX_EDIT)
    if b'\0' in data:
        abort(400, "That isn't a text file; download it instead")
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        abort(400, "That isn't a text file; download it instead")


def download(server_id, path):
    """(an open file, its name) to send, which the response closes. server.properties comes masked."""
    parts, name = split(path)
    if is_properties(parts, name):
        return io.BytesIO(read_bytes(server_id, path)), name
    with file_errors(), opened_dir(server_id, parts) as fd:
        return open_file(fd, name), name


def write(server_id, path, chunks, rcon_password=''):
    """Write a file from an iterable of bytes, in full or not at all: into a hidden temporary file
    beside it, then renamed over it, keeping an old file's permissions. A link there is replaced,
    never written through. server.properties gets its real RCON password back in place of the mask."""
    parts, name = split(path)
    temp = f'.mcpanel-{secrets.token_hex(6)}.tmp'
    with file_errors(), opened_dir(server_id, parts) as fd:
        try:
            old = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            old = None
        if old and stat.S_ISDIR(old.st_mode):
            abort(400, "That's a folder")
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        try:
            with os.fdopen(out, 'wb') as file:
                written = 0
                for chunk in chunks:
                    written += len(chunk)
                    if written > MAX_UPLOAD:
                        abort(413, f'Files can be at most {MAX_UPLOAD // 1024 ** 3} GB')
                    file.write(chunk)
                if old and stat.S_ISREG(old.st_mode):
                    os.fchmod(file.fileno(), stat.S_IMODE(old.st_mode))
            if is_properties(parts, name) and rcon_password:
                restore_password(fd, temp, rcon_password)
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            with suppress(FileNotFoundError):
                os.unlink(temp, dir_fd=fd)
            raise


def restore_password(dir_fd, temp, password):
    """Put the real RCON password back where a saved server.properties has the mask."""
    with open(os.open(temp, os.O_RDWR | os.O_NOFOLLOW, dir_fd=dir_fd), 'r+b') as file:
        text = file.read().decode('utf-8', 'replace')
        text = RCON_PASSWORD.sub(lambda match: match[1] + password if match[0].endswith(MASK) else match[0], text)
        file.seek(0)
        file.truncate()
        file.write(text.encode())


def make_folder(server_id, path):
    parts, name = split(path)
    with file_errors(), opened_dir(server_id, parts) as fd:
        os.mkdir(name, 0o755, dir_fd=fd)


def rename(server_id, path, to):
    """Move or rename within the server's folder. Never onto something that's already there."""
    parts, name = split(path)
    to_parts, to_name = split(to)
    with file_errors(), opened_dir(server_id, parts) as fd, opened_dir(server_id, to_parts) as to_fd:
        os.stat(name, dir_fd=fd, follow_symlinks=False)  # a 404 for a missing source, before the check below
        try:
            os.stat(to_name, dir_fd=to_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            abort(409, 'Something with that name is already there')
        os.rename(name, to_name, src_dir_fd=fd, dst_dir_fd=to_fd)


def delete(server_id, path):
    """Delete a file, a link (not what it points to) or a folder and everything in it."""
    parts, name = split(path)
    with file_errors(), opened_dir(server_id, parts) as fd:
        if stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
            shutil.rmtree(name, dir_fd=fd)  # works by descriptor and never follows a link
        else:
            os.unlink(name, dir_fd=fd)
