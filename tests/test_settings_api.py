"""Changing a server: its container's settings, its game settings, and deleting it."""
import json

import pytest

from mcpanel import accounts, config, files, properties

from .test_servers_api import SURVIVAL, create, grant

CHANGE = {key: SURVIVAL[key] for key in ('name', 'type', 'version', 'java', 'memory_gb')}


def change(client, **fields):
    return client.put('/api/servers/survival', json={**CHANGE, **fields})


def env(docker, name='mcpanel-survival'):
    return dict(item.partition('=')[::2] for item in docker.live(name)['Config']['Env'])


def test_a_change_makes_the_container_again_with_the_same_port_and_folder(admin, docker):
    create(admin)
    create(admin, name='Creative')
    response = change(admin, name='Survival 2', type='FABRIC', version='1.20.1', java='', memory_gb=6)
    assert response.status_code == 200
    server = response.get_json()
    assert server == {**server, 'id': 'survival', 'name': 'Survival 2', 'type': 'FABRIC', 'version': '1.20.1',
                      'java': 'java17', 'memory_gb': 6, 'port': 25565, 'status': 'stopped'}
    assert env(docker)['MEMORY'] == '4608M' and env(docker)['EULA'] == 'TRUE'
    assert docker.live('mcpanel-survival')['HostConfig']['Binds'] == [f'{config.SERVERS_DIR / "survival"}:/data:rw']
    assert [action for action, name, _ in docker.actions if 'survival' in name] == ['rename', 'remove']
    assert [s['id'] for s in admin.get('/api/servers').get_json()['servers']] == ['creative', 'survival']
    assert docker.pulled[-1] == 'itzg/minecraft-server:java17'  # a new tag is pulled


def test_the_same_java_is_not_pulled_again(admin, docker):
    create(admin)
    pulled = list(docker.pulled)
    assert change(admin, version='1.21.5').status_code == 200
    assert docker.pulled == pulled


def test_a_running_server_must_stop_first(admin, docker):
    create(admin)
    admin.post('/api/servers/survival/start')
    response = change(admin, name='New')
    assert response.status_code == 409
    assert response.get_json() == {'error': 'Stop the server before changing these settings'}
    response = admin.delete('/api/servers/survival')
    assert response.status_code == 409 and response.get_json() == {'error': 'Stop the server before deleting it'}


def test_a_refused_create_leaves_the_server_as_it_was(admin, docker, monkeypatch):
    create(admin)
    before = docker.live('mcpanel-survival')['Id']

    def refuse(*args, **kwargs):
        from docker.errors import APIError
        raise APIError('500 Server Error', explanation='no space left on device')
    monkeypatch.setattr(docker.api, 'create_container', refuse)
    response = change(admin, name='New')
    assert response.status_code == 502 and response.get_json() == {'error': 'Docker refused: no space left on device'}
    assert docker.live('mcpanel-survival')['Id'] == before
    assert admin.get('/api/servers').get_json()['servers'][0]['name'] == 'Survival'


def test_only_admins_change_the_server(admin, member, docker):
    create(admin, cpus=2)
    grant(admin, member, 'survival')
    before = docker.live('mcpanel-survival')['Id']
    for fields in ({}, {'name': 'Mine'}, {'version': '1.20.1'}, {'type': 'PURPUR'}, {'java': 'java25'},
                   {'memory_gb': 8}, {'cpus': None}):
        response = change(member, **{'cpus': 2, **fields})
        assert response.status_code == 403 and response.get_json() == {'error': 'Admins only'}
    assert docker.live('mcpanel-survival')['Id'] == before
    assert change(admin, name='Mine', memory_gb=8).get_json()['memory_gb'] == 8


def test_admins_set_or_clear_the_cpu_limit(admin, docker):
    create(admin, cpus=2)
    assert change(admin, cpus=1.5).get_json()['cpus'] == 1.5
    assert docker.live('mcpanel-survival')['HostConfig']['NanoCpus'] == 15 * 10 ** 8
    assert change(admin, cpus=None).get_json()['cpus'] is None
    assert 'NanoCpus' not in docker.live('mcpanel-survival')['HostConfig']


@pytest.mark.parametrize('fields, error', [
    ({'name': ''}, 'Give the server a name'),
    ({'type': 'SPIGOT'}, 'Unknown server type'),
    ({'version': 'x'}, 'Version must be LATEST or a Minecraft release like 26.1'),
    ({'memory_gb': 99}, 'Memory must be a whole number of GB from 1 to 32'),
    ({'cpus': 9}, 'CPU must be a number of cores from 0.5 to 8, or empty for no limit'),
])
def test_bad_changes_are_400(admin, docker, fields, error):
    create(admin)
    response = change(admin, **fields)
    assert response.status_code == 400 and response.get_json() == {'error': error}


def test_servers_a_member_may_not_use_look_missing(admin, member, docker):
    create(admin)
    for response in (member.get('/api/servers/survival/properties'),
                     member.put('/api/servers/survival/properties', json={'pvp': False})):
        assert response.status_code == 404 and response.get_json() == {'error': 'No such server'}


def test_delete_removes_the_container_folder_and_access(admin, member, docker, data_dir):
    create(admin)
    grant(admin, member, 'survival')
    (config.SERVERS_DIR / 'survival' / 'world').mkdir()
    assert admin.delete('/api/servers/survival').status_code == 200
    assert not (config.SERVERS_DIR / 'survival').exists()
    assert admin.get('/api/servers').get_json() == {'servers': []}
    assert accounts.servers_for(member.user) == set()
    # The id is free again, and the member isn't given the new server that takes it.
    assert create(admin).get_json()['id'] == 'survival'
    assert member.get('/api/servers').get_json() == {'servers': []}
    lines = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert ('server_deleted', 'survival') in [(line['event'], line.get('server')) for line in lines]


def test_platform_admins_make_change_and_delete_servers_as_admins_do(platform_admin, member, docker):
    assert create(platform_admin).status_code == 201
    grant(platform_admin, member, 'survival')
    assert member.delete('/api/servers/survival').status_code == 403  # a member can't delete a server of theirs
    assert change(platform_admin, memory_gb=8).get_json()['memory_gb'] == 8
    assert change(platform_admin, cpus=2).get_json()['cpus'] == 2
    assert platform_admin.get('/api/servers/host').status_code == 200
    assert platform_admin.delete('/api/servers/survival').status_code == 200
    assert platform_admin.get('/api/servers').get_json() == {'servers': []}


def test_game_settings_have_the_servers_defaults_before_its_first_start(admin):
    create(admin)
    values = admin.get('/api/servers/survival/properties').get_json()
    assert values == {key: default for key, (default, _) in properties.SETTINGS.items()}


def test_game_settings_are_read_and_saved_keeping_every_other_line(admin, docker):
    create(admin)
    path = config.SERVERS_DIR / 'survival' / 'server.properties'
    password = env(docker)['RCON_PASSWORD']
    path.write_text(f'#Minecraft server properties\nmotd=\\u00a76Hello\\: there\nrcon.password={password}\n'
                    'view-distance=12\npvp=false\nlevel-name=world\n')
    values = admin.get('/api/servers/survival/properties').get_json()
    assert (values['motd'], values['view-distance'], values['pvp']) == ('§6Hello: there', 12, False)
    response = admin.put('/api/servers/survival/properties',
                         json={'motd': '§aNew one\nsecond line ✓', 'view-distance': 8, 'white-list': True})
    assert response.status_code == 200 and response.get_json()['view-distance'] == 8
    assert path.read_text() == ('#Minecraft server properties\nmotd=\\u00a7aNew one\\nsecond line \\u2713\n'
                                f'rcon.password={password}\nview-distance=8\npvp=false\nlevel-name=world\n'
                                'white-list=true\n')
    assert admin.get('/api/servers/survival/properties').get_json()['motd'] == '§aNew one\nsecond line ✓'


def test_saving_before_the_first_start_writes_the_file(admin, docker):
    create(admin)
    assert admin.put('/api/servers/survival/properties', json={'level-seed': '12345', 'difficulty': 'hard'}).status_code == 200
    assert (config.SERVERS_DIR / 'survival' / 'server.properties').read_text() == 'difficulty=hard\nlevel-seed=12345\n'


def test_the_real_rcon_password_stays_in_the_file(admin, docker):
    create(admin)
    path = config.SERVERS_DIR / 'survival' / 'server.properties'
    password = env(docker)['RCON_PASSWORD']
    path.write_text(f'rcon.password={password}\npvp=true\n')
    admin.put('/api/servers/survival/properties', json={'pvp': False})
    assert path.read_text() == f'rcon.password={password}\npvp=false\n'
    assert files.MASK not in path.read_text()


@pytest.mark.parametrize('changes, error', [
    ({'nope': 1}, 'Unknown setting nope'),
    ({'pvp': 'yes'}, 'pvp is true or false'),
    ({'view-distance': 99}, 'view-distance is a whole number from 3 to 32'),
    ({'max-players': True}, 'max-players is a whole number from 1 to 1000'),
    ({'difficulty': 'insane'}, 'difficulty is one of peaceful, easy, normal, hard'),
    ({'motd': 'x' * 201}, 'motd is text of at most 200 characters'),
    ({'motd': 'a\x00b'}, 'motd is text of at most 200 characters'),
    ({'level-seed': 'a\nb'}, 'The seed is one line'),
])
def test_bad_game_settings_are_400(admin, docker, changes, error):
    create(admin)
    response = admin.put('/api/servers/survival/properties', json=changes)
    assert response.status_code == 400 and response.get_json() == {'error': error}
    assert not (config.SERVERS_DIR / 'survival' / 'server.properties').exists()


def test_members_change_game_settings_on_their_servers(admin, member, docker):
    create(admin)
    grant(admin, member, 'survival')
    assert member.put('/api/servers/survival/properties', json={'view-distance': 16}).get_json()['view-distance'] == 16


@pytest.mark.parametrize('version, game_rule', [
    ('1.20.1', False), ('1.21', False), ('1.21.8', False), ('1.21.9', True), ('1.21.11', True), ('26.1', True),
    ('LATEST', True),
])
def test_pvp_is_a_game_rule_from_1_21_9(version, game_rule):
    assert properties.pvp_is_game_rule(version) is game_rule


def pvp_rule(rcon_server, value, wording='Gamerule pvp is {} set to: {}'):
    """Answer gamerule pvp as a server whose pvp game rule is value, which the command can change."""
    rule = {'pvp': value}

    def answer(command):
        if command == 'gamerule pvp':
            return wording.format('currently', rule['pvp'])
        rule['pvp'] = command.removeprefix('gamerule pvp ')
        return wording.format('now', rule['pvp'])
    rcon_server.answers['gamerule'] = answer


def test_pvp_is_the_running_servers_game_rule_from_1_21_9(admin, docker, rcon_server):
    create(admin, version='1.21.9', java='')
    admin.post('/api/servers/survival/start')
    path = config.SERVERS_DIR / 'survival' / 'server.properties'
    path.write_text('pvp=false\nmotd=Hi\n')  # a line 1.21.9 ignores
    pvp_rule(rcon_server, 'true')
    assert admin.get('/api/servers/survival/properties').get_json()['pvp'] is True
    response = admin.put('/api/servers/survival/properties', json={'pvp': False, 'motd': 'New'})
    assert response.status_code == 200 and response.get_json()['pvp'] is False
    assert rcon_server.commands[-2:] == ['gamerule pvp false', 'gamerule pvp']
    assert path.read_text() == 'pvp=false\nmotd=New\n'
    # 26.x words it differently, and says "already" for the value it has.
    rcon_server.answers['gamerule'] = 'Game rule pvp is already set to false'
    assert admin.put('/api/servers/survival/properties', json={'pvp': False}).get_json()['pvp'] is False


def test_pvp_cant_change_while_the_server_cant_say_from_1_21_9(admin, docker, rcon_server):
    create(admin, version='LATEST', java='')
    path = config.SERVERS_DIR / 'survival' / 'server.properties'
    path.write_text('pvp=true\n')
    error = {'error': "PvP can only change while the server is running. From Minecraft 1.21.9 it's a game rule, "
                      'kept in the world.'}
    assert admin.get('/api/servers/survival/properties').get_json()['pvp'] is None
    response = admin.put('/api/servers/survival/properties', json={'pvp': False, 'motd': 'New'})
    assert response.status_code == 409 and response.get_json() == error
    assert path.read_text() == 'pvp=true\n'  # nothing saved
    assert rcon_server.commands == []
    assert admin.put('/api/servers/survival/properties', json={'motd': 'New'}).get_json()['pvp'] is None
    assert path.read_text() == 'pvp=true\nmotd=New\n'
    # Running, but its RCON not answering yet: the same.
    admin.post('/api/servers/survival/start')
    rcon_server.refuse = True
    assert admin.get('/api/servers/survival/properties').get_json()['pvp'] is None
    response = admin.put('/api/servers/survival/properties', json={'pvp': False})
    assert response.status_code == 409 and response.get_json() == error
