import json

import pytest

from mcpanel import rcon, servers

from .test_servers_api import create, grant


def password(docker):
    return next(item for item in docker.live('mcpanel-survival')['Config']['Env']
                if item.startswith('RCON_PASSWORD=')).partition('=')[2]


@pytest.fixture
def running(admin, docker):
    create(admin)
    admin.post('/api/servers/survival/start')


def test_output_comes_from_dockers_log_then_only_whats_new(admin, docker, running):
    docker.output['mcpanel-survival'] = [
        ('2026-10-10T16:00:00.1Z', '[16:00:00] [Server thread/INFO]: Starting'),
        ('2026-10-10T16:00:01.123456789Z', '\x1b[32m[16:00:01] [Server thread/INFO]: Done (3.2s)!\x1b[0m\r'),
    ]
    first = admin.get('/api/servers/survival/console').get_json()
    assert first['lines'] == ['[16:00:00] [Server thread/INFO]: Starting', '[16:00:01] [Server thread/INFO]: Done (3.2s)!']
    assert admin.get('/api/servers/survival/console', query_string={'since': first['since']}).get_json() == \
        {'lines': [], 'since': first['since']}
    docker.output['mcpanel-survival'].append(('2026-10-10T16:00:02Z', 'Steve joined the game'))
    later = admin.get('/api/servers/survival/console', query_string={'since': first['since']}).get_json()
    assert later['lines'] == ['Steve joined the game'] and later['since'] > first['since']


def test_output_is_capped_and_masks_the_rcon_password(admin, docker, running, monkeypatch):
    monkeypatch.setattr(servers, 'LOG_LINES', 2)
    docker.output['mcpanel-survival'] = [(f'2026-10-10T16:00:0{i}Z', f'line {i}') for i in range(4)]
    docker.output['mcpanel-survival'].append(('2026-10-10T16:00:09Z', f'rcon password is {password(docker)}'))
    assert admin.get('/api/servers/survival/console').get_json()['lines'] == ['line 3', 'rcon password is ********']


def test_a_command_goes_over_rcon_with_the_servers_password(admin, docker, running, rcon_server, data_dir):
    rcon_server.answers['list'] = '§6There are 0 of a max of 20 players online: '
    response = admin.post('/api/servers/survival/console', json={'command': '/list'})
    assert response.status_code == 200
    assert response.get_json() == {'response': 'There are 0 of a max of 20 players online: '}
    assert rcon_server.commands == ['list'] and rcon_server.passwords == [password(docker)]
    lines = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert ('server_command', 'list', 'admin') in [(line['event'], line.get('command'), line.get('user')) for line in lines]
    assert password(docker) not in (data_dir / 'logs' / 'audit.log').read_text()


def test_a_long_answer_in_several_packets_comes_whole(admin, running, rcon_server):
    rcon_server.answers['help'] = 'x' * 10000
    assert admin.post('/api/servers/survival/console', json={'command': 'help'}).get_json() == {'response': 'x' * 10000}


def test_a_server_that_isnt_listening_yet_is_502(admin, running, monkeypatch):
    monkeypatch.setattr(rcon, 'address', lambda server_id: ('127.0.0.1', 1))
    response = admin.post('/api/servers/survival/console', json={'command': 'list'})
    assert response.status_code == 502
    assert response.get_json() == {'error': "The server's console isn't answering yet; it may still be starting"}


def test_a_refused_password_is_502(admin, running, rcon_server):
    rcon_server.refuse = True
    response = admin.post('/api/servers/survival/console', json={'command': 'list'})
    assert response.status_code == 502 and 'RCON password' in response.get_json()['error']
    assert rcon_server.commands == []


def test_a_stopped_server_takes_no_commands(admin, docker, rcon_server):
    create(admin)
    response = admin.post('/api/servers/survival/console', json={'command': 'list'})
    assert response.status_code == 409 and response.get_json() == {'error': 'Start the server to send it commands'}


@pytest.mark.parametrize('command, error', [
    ('', 'Type a command'), ('  /  ', 'Type a command'), (None, 'Type a command'),
    ('say a\nop me', 'A command is one line of at most 1446 bytes'),
    ('say ' + 'x' * 1443, 'A command is one line of at most 1446 bytes'),
])
def test_bad_commands_are_400(admin, running, rcon_server, command, error):
    response = admin.post('/api/servers/survival/console', json={'command': command})
    assert response.status_code == 400 and response.get_json() == {'error': error}
    assert rcon_server.commands == []


def test_members_use_only_their_servers_console(admin, member, running, rcon_server):
    assert member.get('/api/servers/survival/console').status_code == 404
    assert member.post('/api/servers/survival/console', json={'command': 'op member'}).status_code == 404
    assert rcon_server.commands == []
    grant(admin, member, 'survival')
    assert member.post('/api/servers/survival/console', json={'command': 'list'}).status_code == 200
