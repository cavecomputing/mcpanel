import json
import os

import pytest

from mcpanel import config, files

from .test_servers_api import create, grant


@pytest.fixture
def folder(admin):
    """The Survival server's folder, with a few files in it."""
    assert create(admin).status_code == 201
    folder = config.SERVERS_DIR / 'survival'
    (folder / 'world').mkdir()
    (folder / 'world' / 'level.dat').write_bytes(b'\0\1level')
    (folder / 'ops.json').write_text('[]')
    (folder / '.rcon-cli.env').write_text('password=hunter2hunter2')
    (folder / 'server.properties').write_text('motd=Hi\nrcon.password=hunter2hunter2\nview-distance=10\n')
    return folder


def listing(client, path=''):
    return client.get('/api/servers/survival/files', query_string={'path': path})


def test_a_listing_has_folders_first_and_no_rcon_files(admin, folder):
    response = listing(admin)
    assert response.status_code == 200
    entries = response.get_json()['entries']
    assert [(entry['name'], entry['type']) for entry in entries] == [
        ('world', 'dir'), ('ops.json', 'file'), ('server.properties', 'file')]
    assert entries[1]['size'] == 2 and entries[0]['size'] is None and entries[1]['modified'] > 0
    assert listing(admin, 'world').get_json() == {
        'path': 'world', 'entries': [{**listing(admin, 'world').get_json()['entries'][0], 'name': 'level.dat'}]}


def test_the_rcon_password_never_leaves(admin, folder):
    for path in ('.rcon-cli.env', '.rcon-cli.yaml', '/.rcon-cli.env'):
        assert admin.get('/api/servers/survival/files/content', query_string={'path': path}).status_code == 404
        assert admin.get('/api/servers/survival/files/download', query_string={'path': path}).status_code == 404
        assert admin.put(f'/api/servers/survival/files/content?path={path}', data=b'x').status_code == 404
        assert admin.delete('/api/servers/survival/files', query_string={'path': path}).status_code == 404
    text = admin.get('/api/servers/survival/files/content', query_string={'path': 'server.properties'}).get_json()['text']
    assert text == f'motd=Hi\nrcon.password={files.MASK}\nview-distance=10\n'
    download = admin.get('/api/servers/survival/files/download', query_string={'path': 'server.properties'})
    assert b'hunter2' not in download.data and files.MASK.encode() in download.data
    response = admin.post('/api/servers/survival/files/rename', json={'path': '.rcon-cli.env', 'to': 'x.txt'})
    assert response.status_code == 404


def test_saving_server_properties_puts_the_real_password_back(admin, folder, docker):
    password = docker.live('mcpanel-survival')['Config']['Env']
    password = next(item for item in password if item.startswith('RCON_PASSWORD=')).partition('=')[2]
    text = f'motd=New\nrcon.password={files.MASK}\n'
    assert admin.put('/api/servers/survival/files/content?path=server.properties', data=text.encode()).status_code == 200
    assert (folder / 'server.properties').read_text() == f'motd=New\nrcon.password={password}\n'


def test_read_write_and_download(admin, folder):
    response = admin.put('/api/servers/survival/files/content?path=config/new.yml', data=b'a: 1')
    assert response.status_code == 404  # no such folder
    assert admin.post('/api/servers/survival/files/folder', json={'path': 'config'}).status_code == 201
    assert admin.post('/api/servers/survival/files/folder', json={'path': 'config'}).status_code == 409
    assert admin.put('/api/servers/survival/files/content?path=config/new.yml', data=b'a: 1').status_code == 200
    assert (folder / 'config' / 'new.yml').read_text() == 'a: 1'
    assert admin.get('/api/servers/survival/files/content?path=config/new.yml').get_json() == {'text': 'a: 1'}
    os.chmod(folder / 'config' / 'new.yml', 0o600)
    admin.put('/api/servers/survival/files/content?path=config/new.yml', data=b'a: 2')
    assert oct(os.stat(folder / 'config' / 'new.yml').st_mode & 0o777) == '0o600'  # kept
    assert [name for name in os.listdir(folder / 'config')] == ['new.yml']  # no temporary file left

    download = admin.get('/api/servers/survival/files/download?path=world/level.dat')
    assert download.status_code == 200 and download.data == b'\0\1level'
    assert download.mimetype == 'application/octet-stream'
    assert download.headers['Content-Disposition'].startswith('attachment')
    assert download.headers['X-Content-Type-Options'] == 'nosniff'
    download.close()
    response = admin.get('/api/servers/survival/files/content?path=world/level.dat')
    assert response.status_code == 400 and response.get_json() == {'error': "That isn't a text file; download it instead"}
    assert admin.get('/api/servers/survival/files/content?path=world').status_code == 400


def test_a_big_file_is_too_big_to_edit(admin, folder, monkeypatch):
    monkeypatch.setattr(files, 'MAX_EDIT', 3)
    assert admin.get('/api/servers/survival/files/content?path=ops.json').status_code == 200
    (folder / 'ops.json').write_text('[ ]')
    assert admin.get('/api/servers/survival/files/content?path=ops.json').status_code == 200
    (folder / 'ops.json').write_text('[  ]')
    assert admin.get('/api/servers/survival/files/content?path=ops.json').status_code == 413


def test_an_upload_over_the_limit_leaves_nothing(admin, folder, monkeypatch):
    monkeypatch.setattr(files, 'MAX_UPLOAD', 4)
    monkeypatch.setattr(files, 'CHUNK', 2)
    assert admin.put('/api/servers/survival/files/content?path=a.jar', data=b'1234').status_code == 200
    assert admin.put('/api/servers/survival/files/content?path=b.jar', data=b'12345').status_code == 413
    (folder / 'ops.json').write_text('[]')
    assert sorted(os.listdir(folder)) == ['.rcon-cli.env', 'a.jar', 'ops.json', 'server.properties', 'world']


def test_rename_and_delete(admin, folder):
    rename = lambda path, to: admin.post('/api/servers/survival/files/rename', json={'path': path, 'to': to})  # noqa: E731
    assert rename('ops.json', 'world/ops.json').status_code == 200
    assert (folder / 'world' / 'ops.json').exists() and not (folder / 'ops.json').exists()
    assert rename('world/ops.json', 'world/level.dat').status_code == 409  # never over something
    assert rename('missing', 'x').status_code == 404
    assert rename('world', 'world/inner').status_code == 400
    assert rename('world', 'world2').status_code == 200
    assert admin.delete('/api/servers/survival/files?path=world2').status_code == 200
    assert not (folder / 'world2').exists()
    assert admin.delete('/api/servers/survival/files?path=').status_code == 400  # never the folder itself
    assert admin.delete('/api/servers/survival/files?path=nope').status_code == 404


@pytest.mark.parametrize('path', ['..', '../survival', 'world/../..', 'world/./level.dat', 'a\0b'])
def test_paths_can_only_go_down(admin, folder, path):
    for response in (listing(admin, path),
                     admin.get('/api/servers/survival/files/content', query_string={'path': path}),
                     admin.delete('/api/servers/survival/files', query_string={'path': path})):
        assert response.status_code == 400, response.get_json()


def test_links_are_never_followed(admin, folder, tmp_path):
    secret = tmp_path / 'host-secret'
    secret.write_text('root:x:0:0')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'f').write_text('host file')
    os.symlink(secret, folder / 'passwd')
    os.symlink(outside, folder / 'escape')
    os.symlink(outside, folder / 'world' / 'escape')
    os.mkfifo(folder / 'fifo')
    entries = {entry['name']: entry['type'] for entry in listing(admin).get_json()['entries']}
    assert entries['passwd'] == 'link' and entries['escape'] == 'link' and entries['fifo'] == 'other'
    for path in ('passwd', 'escape/f', 'world/escape/f', 'fifo'):
        assert admin.get('/api/servers/survival/files/content', query_string={'path': path}).status_code == 400
        assert admin.get('/api/servers/survival/files/download', query_string={'path': path}).status_code == 400
    assert listing(admin, 'escape').status_code == 400
    assert admin.put('/api/servers/survival/files/content?path=escape/f', data=b'owned').status_code == 400
    assert admin.post('/api/servers/survival/files/folder', json={'path': 'escape/new'}).status_code == 400
    assert admin.post('/api/servers/survival/files/rename', json={'path': 'ops.json', 'to': 'escape/ops.json'}).status_code == 400
    # Saving over a link replaces the link, not the file it points to.
    assert admin.put('/api/servers/survival/files/content?path=passwd', data=b'new').status_code == 200
    assert secret.read_text() == 'root:x:0:0' and not os.path.islink(folder / 'passwd')
    # Deleting a link deletes the link only.
    assert admin.delete('/api/servers/survival/files?path=escape').status_code == 200
    assert (outside / 'f').read_text() == 'host file'


def test_members_reach_only_their_servers_files(admin, member, folder):
    for response in (listing(member), member.get('/api/servers/survival/files/content?path=ops.json'),
                     member.put('/api/servers/survival/files/content?path=x', data=b'x'),
                     member.delete('/api/servers/survival/files?path=ops.json')):
        assert response.status_code == 404 and response.get_json() == {'error': 'No such server'}
    assert (folder / 'ops.json').exists() and not (folder / 'x').exists()
    grant(admin, member, 'survival')
    assert listing(member).status_code == 200
    assert member.put('/api/servers/survival/files/content?path=x', data=b'x').status_code == 200
    assert listing(member, '').status_code == 200 and listing(admin).status_code == 200
    assert admin.get('/api/servers/gone/files').status_code == 404


def test_changes_are_audited(admin, folder, data_dir):
    admin.put('/api/servers/survival/files/content?path=a.txt', data=b'abc')
    admin.post('/api/servers/survival/files/folder', json={'path': 'mods'})
    admin.post('/api/servers/survival/files/rename', json={'path': 'a.txt', 'to': 'mods/a.txt'})
    admin.delete('/api/servers/survival/files?path=mods')
    lines = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert [(line['event'], line.get('path')) for line in lines if line['event'].startswith(('file', 'folder'))] == [
        ('file_written', 'a.txt'), ('folder_created', 'mods'), ('file_renamed', 'a.txt'), ('file_deleted', 'mods')]
