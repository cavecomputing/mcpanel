import io
import json
import os
import re
import tarfile
from datetime import datetime, timezone

import pytest

from mcpanel import backups, config, files, rcon

from .conftest import rcon_password
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


def members_of(data):
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        return {member.name: (tar.extractfile(member).read() if member.isfile() else None) for member in tar}


def test_a_backup_holds_the_folder_without_the_rcon_password(admin, folder):
    response = admin.post('/api/servers/survival/backups')
    assert response.status_code == 201
    backup = response.get_json()
    assert re.fullmatch(r'\d{8}T\d{6}Z-manual\.tar\.gz', backup['name']) and backup['size'] > 0
    assert backup['created'] == int(datetime.strptime(backup['name'][:16], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp())
    assert backup['schedule'] is None
    assert admin.get('/api/servers/survival/backups').get_json() == {'backups': [backup | {'cron': None}]}
    download = admin.get(f'/api/servers/survival/backups/{backup["name"]}')
    assert download.status_code == 200 and download.headers['Content-Disposition'].startswith('attachment')
    content = members_of(download.data)
    download.close()
    assert content == {'server.properties': f'motd=Hi\nrcon.password={files.MASK}\n'.encode(), 'world': None,
                       'world/region': None, 'world/region/r.0.0.mca': b'\0region'}
    assert sorted(os.listdir(config.BACKUPS_DIR / 'survival')) == [backup['name']]  # no partial file left


def test_backups_say_when_and_how_they_were_made_named_either_way(admin, folder):
    schedule = admin.post('/api/servers/survival/schedules', json={'action': 'backup', 'cron': '0 3 * * *',
                                                                   'timezone': 'UTC', 'keep': 7}).get_json()
    names = ['2026-01-01_120000.tar.gz', '20260102T030000Z-manual.tar.gz',  # Back up now's, before and since ISO 8601
             f'2026-01-01_030000-schedule-{schedule["id"]}.tar.gz', '20260103T030000Z-schedule-999.tar.gz']  # 999: removed
    (config.BACKUPS_DIR / 'survival').mkdir(parents=True)
    for name in names:
        (config.BACKUPS_DIR / 'survival' / name).write_bytes(b'')
    listed = admin.get('/api/servers/survival/backups').get_json()['backups']
    assert [(backup['name'], backup['created'], backup['schedule'], backup['cron']) for backup in listed] == [
        (names[3], 1767409200, 999, None), (names[1], 1767322800, None, None),
        (names[0], 1767268800, None, None), (names[2], 1767236400, schedule['id'], '0 3 * * *')]


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
    assert (folder / 'server.properties').read_text() == f'motd=Hi\nrcon.password={rcon_password(docker)}\n'
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
