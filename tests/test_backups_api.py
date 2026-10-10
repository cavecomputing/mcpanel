import io
import json
import os
import tarfile

import pytest

from mcpanel import backups, config, files

from .test_servers_api import create, grant


@pytest.fixture
def folder(admin):
    assert create(admin).status_code == 201
    folder = config.SERVERS_DIR / 'survival'
    (folder / 'world' / 'region').mkdir(parents=True)
    (folder / 'world' / 'region' / 'r.0.0.mca').write_bytes(b'\0region')
    (folder / '.rcon-cli.env').write_text('password=secretsecret')
    (folder / 'server.properties').write_text('motd=Hi\nrcon.password=secretsecret\n')
    return folder


def password(docker):
    return next(item for item in docker.live('mcpanel-survival')['Config']['Env']
                if item.startswith('RCON_PASSWORD=')).partition('=')[2]


def members_of(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {member.name: (tar.extractfile(member).read() if member.isfile() else None) for member in tar}


def test_a_backup_holds_the_folder_without_the_rcon_password(admin, folder):
    response = admin.post('/api/servers/survival/backups')
    assert response.status_code == 201
    backup = response.get_json()
    assert backups.NAME.fullmatch(backup['name']) and backup['size'] > 0
    assert admin.get('/api/servers/survival/backups').get_json() == {'backups': [backup]}
    download = admin.get(f'/api/servers/survival/backups/{backup["name"]}')
    assert download.status_code == 200 and download.headers['Content-Disposition'].startswith('attachment')
    content = members_of(download.data)
    download.close()
    assert content == {'server.properties': f'motd=Hi\nrcon.password={files.MASK}\n'.encode(), 'world': None,
                       'world/region': None, 'world/region/r.0.0.mca': b'\0region'}
    assert sorted(os.listdir(config.BACKUPS_DIR / 'survival')) == [backup['name']]  # no partial file left


def test_a_backup_leaves_links_out(admin, folder, tmp_path):
    (tmp_path / 'host').mkdir()
    (tmp_path / 'host' / 'shadow').write_text('root:secret')
    os.symlink(tmp_path / 'host', folder / 'escape')
    os.symlink(tmp_path / 'host' / 'shadow', folder / 'world' / 'shadow')
    os.mkfifo(folder / 'fifo')
    name = admin.post('/api/servers/survival/backups').get_json()['name']
    content = members_of((config.BACKUPS_DIR / 'survival' / name).read_bytes())
    assert not {'escape', 'world/shadow', 'fifo'} & set(content)
    assert b'root:secret' not in (config.BACKUPS_DIR / 'survival' / name).read_bytes()


def test_a_running_server_saves_first_and_writes_again_after(admin, folder, rcon_server):
    admin.post('/api/servers/survival/start')
    assert admin.post('/api/servers/survival/backups').status_code == 201
    assert rcon_server.commands == ['save-off', 'save-all flush', 'save-on']


def test_a_server_that_cant_be_told_to_save_is_backed_up_anyway(admin, folder, monkeypatch):
    from mcpanel import rcon
    monkeypatch.setattr(rcon, 'address', lambda server_id: ('127.0.0.1', 1))
    admin.post('/api/servers/survival/start')
    assert admin.post('/api/servers/survival/backups').status_code == 201


def test_restore_puts_the_backup_in_place_with_the_real_password(admin, folder, docker):
    name = admin.post('/api/servers/survival/backups').get_json()['name']
    (folder / 'world' / 'region' / 'r.0.0.mca').write_bytes(b'griefed')
    (folder / 'new.txt').write_text('after the backup')
    assert admin.post(f'/api/servers/survival/backups/{name}/restore').status_code == 200
    assert (folder / 'world' / 'region' / 'r.0.0.mca').read_bytes() == b'\0region'
    assert not (folder / 'new.txt').exists()
    assert (folder / 'server.properties').read_text() == f'motd=Hi\nrcon.password={password(docker)}\n'
    assert sorted(os.listdir(config.SERVERS_DIR)) == ['survival']  # nothing left beside it


def test_restore_needs_the_server_stopped(admin, folder):
    name = admin.post('/api/servers/survival/backups').get_json()['name']
    admin.post('/api/servers/survival/start')
    response = admin.post(f'/api/servers/survival/backups/{name}/restore')
    assert response.status_code == 409 and response.get_json() == {'error': 'Stop the server before restoring a backup'}


def test_a_backup_with_a_path_out_is_refused_and_changes_nothing(admin, folder):
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode='w:gz') as tar:
        entry = tarfile.TarInfo('../../escaped')
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b'x'))
    (config.BACKUPS_DIR / 'survival').mkdir(parents=True)
    (config.BACKUPS_DIR / 'survival' / '2026-01-01_000000.tar.gz').write_bytes(evil.getvalue())
    response = admin.post('/api/servers/survival/backups/2026-01-01_000000.tar.gz/restore')
    assert response.status_code == 422 and response.get_json()['error'].startswith("That backup can't be restored")
    assert (folder / 'world').is_dir() and not (config.DATA_DIR / 'escaped').exists()
    assert sorted(os.listdir(config.SERVERS_DIR)) == ['survival']


@pytest.mark.parametrize('name', ['nope.tar.gz', '..', '2026-01-01_000000.tar.gz', '%2e%2e%2fmcpanel.db'])
def test_unknown_backups_are_404(admin, folder, name):
    assert admin.get(f'/api/servers/survival/backups/{name}').status_code == 404
    assert admin.delete(f'/api/servers/survival/backups/{name}').status_code == 404
    assert admin.post(f'/api/servers/survival/backups/{name}/restore').status_code == 404


def test_delete_and_one_at_a_time(admin, folder):
    name = admin.post('/api/servers/survival/backups').get_json()['name']
    backups.LOCKS['survival'].acquire()
    try:
        response = admin.post('/api/servers/survival/backups')
        assert response.status_code == 409
    finally:
        backups.LOCKS['survival'].release()
    assert admin.delete(f'/api/servers/survival/backups/{name}').status_code == 200
    assert admin.get('/api/servers/survival/backups').get_json() == {'backups': []}


def test_deleting_the_server_deletes_its_backups(admin, folder):
    admin.post('/api/servers/survival/backups')
    assert admin.delete('/api/servers/survival').status_code == 200
    assert not (config.BACKUPS_DIR / 'survival').exists()


def test_members_reach_only_their_servers_backups(admin, member, folder, data_dir):
    name = admin.post('/api/servers/survival/backups').get_json()['name']
    for response in (member.get('/api/servers/survival/backups'), member.post('/api/servers/survival/backups'),
                     member.get(f'/api/servers/survival/backups/{name}'),
                     member.delete(f'/api/servers/survival/backups/{name}')):
        assert response.status_code == 404
    grant(admin, member, 'survival')
    assert member.post('/api/servers/survival/backups').status_code in (201, 409)  # 409: made this second already
    lines = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert 'backup_created' in [line['event'] for line in lines]
