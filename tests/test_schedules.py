import json
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mcpanel import backups, config, schedules, servers

from .test_servers_api import create, grant

CHICAGO = 'America/Chicago'


@pytest.fixture
def survival(admin):
    assert create(admin).status_code == 201
    return 'survival'


def schedule(client, line='0 4 * * *', zone=CHICAGO, action='restart', keep=7):
    return client.post('/api/servers/survival/schedules', json={'action': action, 'cron': line, 'timezone': zone, 'keep': keep})


def test_add_list_and_remove(admin, survival, clock, data_dir):
    clock['now'] = datetime(2026, 10, 13, 3, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    response = schedule(admin, ' 0  4 * * * ')
    assert response.status_code == 201
    restart = response.get_json()
    next_time = datetime(2026, 10, 13, 4, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    assert restart == {'id': restart['id'], 'action': 'restart', 'cron': '0 4 * * *', 'timezone': CHICAGO, 'keep': None,
                       'next': next_time}  # a restart keeps no backups, whatever was sent
    backup = schedule(admin, '0 3 * * *', action='backup', keep=14).get_json()
    assert (backup['action'], backup['keep']) == ('backup', 14)
    assert admin.get('/api/servers/survival/schedules').get_json() == {'schedules': [restart, backup]}
    assert admin.delete(f'/api/servers/survival/schedules/{restart["id"]}').get_json() == {}
    assert admin.get('/api/servers/survival/schedules').get_json() == {'schedules': [backup]}
    assert admin.delete(f'/api/servers/survival/schedules/{restart["id"]}').status_code == 404
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert [(event['event'], event.get('action'), event.get('cron')) for event in events if 'schedule' in event['event']] == [
        ('schedule_added', 'restart', '0 4 * * *'), ('schedule_added', 'backup', '0 3 * * *'),
        ('schedule_removed', 'restart', '0 4 * * *')]


def test_a_restarts_next_is_after_the_warnings_have_had_time(admin, survival, clock):
    # 3:56 is too late to warn players 5 minutes before 4:00, so the next is tomorrow's. A backup
    # gives no warning.
    clock['now'] = datetime(2026, 10, 13, 3, 56, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    assert schedule(admin).get_json()['next'] == datetime(2026, 10, 14, 4, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    assert schedule(admin, action='backup').get_json()['next'] == datetime(2026, 10, 13, 4, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()


@pytest.mark.parametrize('body, message', [
    ({'cron': '0 25 * * *', 'timezone': CHICAGO}, '"25" is not a valid hour: those go from 0 to 23'),
    ({'cron': '0 4 * *', 'timezone': CHICAGO}, 'five fields'),
    ({'timezone': CHICAGO}, 'five fields'),
    ({'cron': '0 4 30 2 *', 'timezone': CHICAGO}, 'never comes round'),
    ({'cron': '0 4 * * *', 'timezone': 'Mars/Olympus'}, 'Unknown time zone'),
    ({'cron': '0 4 * * *', 'timezone': '../../etc/passwd'}, 'Unknown time zone'),
    ({'cron': '0 4 * * *'}, 'Unknown time zone'),
    ({'action': 'delete', 'cron': '0 4 * * *', 'timezone': CHICAGO}, 'restarts the server or backs it up'),
    ({'action': None, 'cron': '0 4 * * *', 'timezone': CHICAGO}, 'restarts the server or backs it up'),
    *[({'action': 'backup', 'cron': '0 4 * * *', 'timezone': CHICAGO, 'keep': keep}, 'Keep from 1 to 1000 backups')
      for keep in (None, 0, 1001, '7', 7.5, True)],
])
def test_refused_with_why(admin, survival, body, message):
    response = admin.post('/api/servers/survival/schedules', json={'action': 'restart', **body})
    assert response.status_code == 400 and message in response.get_json()['error']


def test_members_schedule_only_their_servers(admin, member, survival):
    restart_id = schedule(admin).get_json()['id']
    for response in (member.get('/api/servers/survival/schedules'), schedule(member),
                     member.delete(f'/api/servers/survival/schedules/{restart_id}')):
        assert response.status_code == 404
    grant(admin, member, 'survival')
    assert schedule(member, '0 */6 * * *').status_code == 201
    assert member.delete(f'/api/servers/survival/schedules/{restart_id}').status_code == 200


def test_another_servers_restart_cant_be_removed_through_this_one(admin, survival):
    assert create(admin, name='Creative').status_code == 201
    other = admin.post('/api/servers/creative/schedules', json={'action': 'restart', 'cron': '0 4 * * *', 'timezone': CHICAGO}).get_json()
    assert admin.delete(f'/api/servers/survival/schedules/{other["id"]}').status_code == 404
    assert len(admin.get('/api/servers/creative/schedules').get_json()['schedules']) == 1


def test_deleting_a_server_drops_its_schedule(admin, survival):
    schedule(admin)
    assert admin.delete('/api/servers/survival').status_code == 200
    assert create(admin).status_code == 201  # the same id again
    assert admin.get('/api/servers/survival/schedules').get_json() == {'schedules': []}


# The scheduler: which countdowns each minute starts, with Chicago's daylight saving changes.

class InlineThread:
    """A thread that runs its target as it starts, so countdowns are recorded in order."""

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def inline_threads(monkeypatch):
    monkeypatch.setattr(schedules, 'threading', SimpleNamespace(Thread=InlineThread))


@pytest.fixture
def started(monkeypatch):
    """The (cron, restart time) of each countdown tick() starts, instead of running it."""
    started = []

    def count_down(row, at):
        started.append((row['cron'], datetime.fromtimestamp(at, ZoneInfo(row['timezone'])).strftime('%m-%d %H:%M')))
        schedules.counting_down.discard(row['server'])

    monkeypatch.setattr(schedules, 'count_down', count_down)
    return started


def run_minutes(start, minutes, zone=CHICAGO):
    """tick() a second past each minute for a while, from a wall-clock time in zone."""
    clocks = {}
    first = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(zone)).timestamp()
    for n in range(minutes):
        schedules.tick(first + 60 * n + 1, clocks)


def test_a_countdown_starts_five_minutes_before_its_time(admin, survival, started):
    schedule(admin, '0 4 * * *')
    schedule(admin, '*/30 * * * *', 'UTC')  # another time zone, kept apart
    run_minutes('2026-10-13 03:50', 20)
    assert sorted(started) == [('*/30 * * * *', '10-13 09:00'), ('0 4 * * *', '10-13 04:00')]  # 9:00 UTC is 4:00 here


def test_a_backup_starts_at_its_time_alongside_a_restart(admin, survival, started, monkeypatch):
    backed_up = []
    monkeypatch.setattr(schedules, 'back_up', lambda row: backed_up.append(
        (row['cron'], datetime.fromtimestamp(time.time(), ZoneInfo(CHICAGO)).strftime('%H:%M:%S'))))
    schedule(admin, '0 4 * * *', action='backup')
    schedule(admin, '0 4 * * *')
    clocks = {}
    for minute in range(50, 60):  # 3:50 to 3:59: the restart's countdown starts at 3:55
        schedules.tick(datetime(2026, 10, 13, 3, minute, 1, tzinfo=ZoneInfo(CHICAGO)).timestamp(), clocks)
    assert started == [('0 4 * * *', '10-13 04:00')] and backed_up == []
    monkeypatch.setattr(time, 'time', lambda: datetime(2026, 10, 13, 4, 0, 1, tzinfo=ZoneInfo(CHICAGO)).timestamp())
    schedules.tick(time.time(), clocks)
    assert backed_up == [('0 4 * * *', '04:00:01')]


def test_one_countdown_at_a_time_per_server(admin, survival, monkeypatch):
    schedule(admin, '0 4 * * *')
    schedule(admin, '0 4 * * *')
    begun = []
    monkeypatch.setattr(schedules, 'count_down', lambda row, at: begun.append(row['id']))  # never finishes
    run_minutes('2026-10-13 03:55', 1)
    assert len(begun) == 1
    schedules.counting_down.clear()


def test_spring_forward_runs_a_skipped_fixed_time_once_and_wildcards_on(admin, survival, started):
    # 14 March 2027: Chicago's clocks go from 1:59 to 3:00.
    schedule(admin, '30 2 * * *')   # in the skipped hour: runs right after the jump
    schedule(admin, '15 * * * *')   # every hour at :15: 2:15 doesn't exist, so it next runs at 3:15
    run_minutes('2027-03-14 01:00', 90)
    assert started == [('15 * * * *', '03-14 01:15'), ('30 2 * * *', '03-14 03:00'),
                       ('15 * * * *', '03-14 03:15')]


def test_fall_back_runs_a_fixed_time_once_and_wildcards_both_times(admin, survival, started):
    # 1 November 2026: Chicago's clocks go from 1:59 back to 1:00, so 1:00-1:59 happens twice.
    schedule(admin, '30 1 * * *')
    schedule(admin, '45 * * * *')
    run_minutes('2026-11-01 00:00', 240)
    assert started == [('45 * * * *', '11-01 00:45'), ('30 1 * * *', '11-01 01:30'),
                       ('45 * * * *', '11-01 01:45'), ('45 * * * *', '11-01 01:45'),
                       ('45 * * * *', '11-01 02:45')]


def test_a_minute_handled_twice_starts_nothing_twice(admin, survival, started):
    schedule(admin, '* * * * *')
    clocks = {}
    now = datetime(2026, 10, 13, 3, 55, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    schedules.tick(now + 1, clocks)
    schedules.tick(now + 30, clocks)
    assert len(started) == 1


def test_a_big_jump_of_the_clock_catches_nothing_up(admin, survival, started):
    schedule(admin, '0 4 * * *')
    clocks = {}
    now = datetime(2026, 10, 13, 3, 30, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    schedules.tick(now, clocks)
    schedules.tick(now + 2 * 60, clocks)       # a couple of minutes late: nothing due
    schedules.tick(now + 30 * 60, clocks)      # 28 minutes late, as after a daylight saving jump: 4:00 caught up
    schedules.tick(now + 10 * 3600, clocks)    # 10 hours on: a correction, nothing caught up
    schedules.tick(now + 34 * 3600, clocks)    # a day after that, past 4:00 again
    assert started == [('0 4 * * *', '10-13 04:05')]


# The countdown itself.

@pytest.fixture
def no_waiting(monkeypatch, clock):
    """time.sleep() moves the clock instead of waiting."""
    monkeypatch.setattr(time, 'sleep', lambda seconds: clock.update(now=clock['now'] + seconds))
    return clock


def countdown_row(admin, line='0 4 * * *'):
    restart = schedule(admin, line).get_json()
    return {'id': restart['id'], 'server': 'survival', 'cron': line, 'timezone': CHICAGO}


def test_players_are_warned_then_the_server_restarts(admin, survival, docker, rcon_server, no_waiting, data_dir):
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    row = countdown_row(admin)
    docker.actions.clear()
    schedules.count_down(row, no_waiting['now'] + schedules.LEADS['restart'])
    assert rcon_server.commands == ['say This server restarts in 5 minutes.', 'say This server restarts in 1 minute.',
                                    'say This server restarts in 10 seconds.']
    assert [action for action, _, _ in docker.actions] == ['restart']
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert {'event': 'server_restarted', 'server': 'survival', 'schedule': '0 4 * * *'}.items() <= events[-1].items()


def test_a_stopped_server_is_left_stopped(admin, survival, docker, rcon_server, no_waiting):
    row = countdown_row(admin)
    schedules.count_down(row, no_waiting['now'] + schedules.LEADS['restart'])
    assert rcon_server.commands == [] and docker.actions == []


def test_a_restart_unscheduled_during_its_countdown_doesnt_happen(admin, survival, docker, rcon_server, no_waiting):
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    row = countdown_row(admin)
    docker.actions.clear()
    admin.delete(f'/api/servers/survival/schedules/{row["id"]}')
    schedules.count_down(row, no_waiting['now'] + schedules.LEADS['restart'])
    assert docker.actions == []


def test_an_unanswered_warning_still_restarts(admin, survival, docker, no_waiting, monkeypatch):
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    row = countdown_row(admin)
    docker.actions.clear()
    monkeypatch.setattr(schedules.rcon, 'address', lambda server_id: ('127.0.0.1', 1))  # nothing listens
    schedules.count_down(row, no_waiting['now'] + schedules.LEADS['restart'])
    assert [action for action, _, _ in docker.actions] == ['restart']


def test_a_server_gone_by_its_restart_is_logged(admin, survival, docker, no_waiting, caplog):
    row = countdown_row(admin)
    docker.reachable = False
    schedules.count_down(row, no_waiting['now'] + schedules.LEADS['restart'])
    assert "Couldn't restart server survival on schedule: Docker is unreachable" in caplog.text
    assert 'survival' not in schedules.counting_down


# A scheduled backup.

def backup_row(admin, keep=7):
    backup = schedule(admin, '0 4 * * *', action='backup', keep=keep).get_json()
    return {'id': backup['id'], 'server': 'survival', 'action': 'backup', 'cron': '0 4 * * *', 'timezone': CHICAGO,
            'keep': keep}


def test_a_scheduled_backup_is_made_as_back_up_now_makes_it(admin, survival, docker, rcon_server, data_dir):
    (config.SERVERS_DIR / 'survival' / 'level.dat').write_bytes(b'world')
    schedules.back_up(backup_row(admin))
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    schedules.back_up(backup_row(admin))
    assert len(admin.get('/api/servers/survival/backups').get_json()['backups']) == 2
    assert rcon_server.commands == ['save-off', 'save-all flush', 'save-on']  # only while it ran
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert [event['schedule'] for event in events if event['event'] == 'backup_created'] == ['0 4 * * *', '0 4 * * *']


def test_a_scheduled_backup_that_cant_be_made_is_logged(admin, survival, caplog):
    with backups.LOCKS['survival']:  # one under way
        schedules.back_up(backup_row(admin))
    assert "Couldn't back up server survival on schedule: A backup or restore of this server is already under way" in caplog.text


def test_a_backup_schedule_deletes_only_its_own_backups_past_the_newest_it_keeps(admin, survival, data_dir):
    row, other = backup_row(admin, keep=2), backup_row(admin, keep=1)
    folder = config.BACKUPS_DIR / 'survival'
    folder.mkdir(parents=True)
    own = [f'2026-01-0{day}_040000-schedule-{row["id"]}.tar.gz' for day in (1, 2, 3)]
    untouched = ['2026-01-01_120000.tar.gz', f'2026-01-01_040000-schedule-{other["id"]}.tar.gz']  # Back up now's, another's
    for name in own + untouched:
        (folder / name).write_bytes(b'')
    schedules.back_up(row)
    listed = admin.get('/api/servers/survival/backups').get_json()['backups']
    made = listed[0]
    assert made['name'].endswith(f'-schedule-{row["id"]}.tar.gz') and made['schedule'] == row['id']
    assert sorted(backup['name'] for backup in listed) == sorted([made['name'], own[2], *untouched])
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert [event['backup'] for event in events if event['event'] == 'backup_deleted'] == [own[1], own[0]]
