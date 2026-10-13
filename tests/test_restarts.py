import json
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from mcpanel import restarts, servers

from .test_servers_api import create, grant

CHICAGO = 'America/Chicago'


@pytest.fixture
def survival(admin):
    assert create(admin).status_code == 201
    return 'survival'


def schedule(client, line='0 4 * * *', zone=CHICAGO):
    return client.post('/api/servers/survival/restarts', json={'cron': line, 'timezone': zone})


def test_add_list_and_remove(admin, survival, clock, data_dir):
    clock['now'] = datetime(2026, 10, 13, 3, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    response = schedule(admin, ' 0  4 * * * ')
    assert response.status_code == 201
    restart = response.get_json()
    next_time = datetime(2026, 10, 13, 4, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    assert restart == {'id': restart['id'], 'cron': '0 4 * * *', 'timezone': CHICAGO, 'next': next_time}
    assert admin.get('/api/servers/survival/restarts').get_json() == {'restarts': [restart]}
    assert admin.delete(f'/api/servers/survival/restarts/{restart["id"]}').get_json() == {}
    assert admin.get('/api/servers/survival/restarts').get_json() == {'restarts': []}
    assert admin.delete(f'/api/servers/survival/restarts/{restart["id"]}').status_code == 404
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert [(event['event'], event.get('cron')) for event in events if 'restart' in event['event']] == [
        ('restart_scheduled', '0 4 * * *'), ('restart_unscheduled', '0 4 * * *')]


def test_next_is_after_the_warnings_have_had_time(admin, survival, clock):
    # 3:56 is too late to warn players 5 minutes before 4:00, so the next is tomorrow's.
    clock['now'] = datetime(2026, 10, 13, 3, 56, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    assert schedule(admin).get_json()['next'] == datetime(2026, 10, 14, 4, 0, tzinfo=ZoneInfo(CHICAGO)).timestamp()


@pytest.mark.parametrize('body, message', [
    ({'cron': '0 25 * * *', 'timezone': CHICAGO}, '"25" is not a valid hour: those go from 0 to 23'),
    ({'cron': '0 4 * *', 'timezone': CHICAGO}, 'five fields'),
    ({'timezone': CHICAGO}, 'five fields'),
    ({'cron': '0 4 30 2 *', 'timezone': CHICAGO}, 'never comes round'),
    ({'cron': '0 4 * * *', 'timezone': 'Mars/Olympus'}, 'Unknown time zone'),
    ({'cron': '0 4 * * *', 'timezone': '../../etc/passwd'}, 'Unknown time zone'),
    ({'cron': '0 4 * * *'}, 'Unknown time zone'),
])
def test_refused_with_why(admin, survival, body, message):
    response = admin.post('/api/servers/survival/restarts', json=body)
    assert response.status_code == 400 and message in response.get_json()['error']


def test_members_schedule_only_their_servers(admin, member, survival):
    restart_id = schedule(admin).get_json()['id']
    for response in (member.get('/api/servers/survival/restarts'), schedule(member),
                     member.delete(f'/api/servers/survival/restarts/{restart_id}')):
        assert response.status_code == 404
    grant(admin, member, 'survival')
    assert schedule(member, '0 */6 * * *').status_code == 201
    assert member.delete(f'/api/servers/survival/restarts/{restart_id}').status_code == 200


def test_another_servers_restart_cant_be_removed_through_this_one(admin, survival):
    assert create(admin, name='Creative').status_code == 201
    other = admin.post('/api/servers/creative/restarts', json={'cron': '0 4 * * *', 'timezone': CHICAGO}).get_json()
    assert admin.delete(f'/api/servers/survival/restarts/{other["id"]}').status_code == 404
    assert len(admin.get('/api/servers/creative/restarts').get_json()['restarts']) == 1


def test_deleting_a_server_drops_its_schedule(admin, survival):
    schedule(admin)
    assert admin.delete('/api/servers/survival').status_code == 200
    assert create(admin).status_code == 201  # the same id again
    assert admin.get('/api/servers/survival/restarts').get_json() == {'restarts': []}


# The scheduler: which countdowns each minute starts, with Chicago's daylight saving changes.

class InlineThread:
    """A thread that runs its target as it starts, so countdowns are recorded in order."""

    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def inline_threads(monkeypatch):
    monkeypatch.setattr(restarts, 'threading', SimpleNamespace(Thread=InlineThread))


@pytest.fixture
def started(monkeypatch):
    """The (cron, restart time) of each countdown tick() starts, instead of running it."""
    started = []

    def count_down(row, at):
        started.append((row['cron'], datetime.fromtimestamp(at, ZoneInfo(row['timezone'])).strftime('%m-%d %H:%M')))
        restarts.counting_down.discard(row['server'])

    monkeypatch.setattr(restarts, 'count_down', count_down)
    return started


def run_minutes(start, minutes, zone=CHICAGO):
    """tick() a second past each minute for a while, from a wall-clock time in zone."""
    clocks = {}
    first = datetime.fromisoformat(start).replace(tzinfo=ZoneInfo(zone)).timestamp()
    for n in range(minutes):
        restarts.tick(first + 60 * n + 1, clocks)


def test_a_countdown_starts_five_minutes_before_its_time(admin, survival, started):
    schedule(admin, '0 4 * * *')
    schedule(admin, '*/30 * * * *', 'UTC')  # another time zone, kept apart
    run_minutes('2026-10-13 03:50', 20)
    assert sorted(started) == [('*/30 * * * *', '10-13 09:00'), ('0 4 * * *', '10-13 04:00')]  # 9:00 UTC is 4:00 here


def test_one_countdown_at_a_time_per_server(admin, survival, monkeypatch):
    schedule(admin, '0 4 * * *')
    schedule(admin, '0 4 * * *')
    begun = []
    monkeypatch.setattr(restarts, 'count_down', lambda row, at: begun.append(row['id']))  # never finishes
    run_minutes('2026-10-13 03:55', 1)
    begun
    assert len(begun) == 1
    restarts.counting_down.clear()


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
    restarts.tick(now + 1, clocks)
    restarts.tick(now + 30, clocks)
    assert len(started) == 1


def test_a_big_jump_of_the_clock_catches_nothing_up(admin, survival, started):
    schedule(admin, '0 4 * * *')
    clocks = {}
    now = datetime(2026, 10, 13, 3, 30, tzinfo=ZoneInfo(CHICAGO)).timestamp()
    restarts.tick(now, clocks)
    restarts.tick(now + 2 * 60, clocks)       # a couple of minutes late: nothing due
    restarts.tick(now + 30 * 60, clocks)      # 28 minutes late, as after a daylight saving jump: 4:00 caught up
    restarts.tick(now + 10 * 3600, clocks)    # 10 hours on: a correction, nothing caught up
    restarts.tick(now + 34 * 3600, clocks)    # a day after that, past 4:00 again
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
    restarts.count_down(row, no_waiting['now'] + restarts.LEAD)
    assert rcon_server.commands == ['say This server restarts in 5 minutes.', 'say This server restarts in 1 minute.',
                                    'say This server restarts in 10 seconds.']
    assert [action for action, _, _ in docker.actions] == ['restart']
    events = [json.loads(line) for line in (data_dir / 'logs' / 'audit.log').read_text().splitlines()]
    assert {'event': 'server_restarted', 'server': 'survival', 'schedule': '0 4 * * *'}.items() <= events[-1].items()


def test_a_stopped_server_is_left_stopped(admin, survival, docker, rcon_server, no_waiting):
    row = countdown_row(admin)
    restarts.count_down(row, no_waiting['now'] + restarts.LEAD)
    assert rcon_server.commands == [] and docker.actions == []


def test_a_restart_unscheduled_during_its_countdown_doesnt_happen(admin, survival, docker, rcon_server, no_waiting):
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    row = countdown_row(admin)
    docker.actions.clear()
    admin.delete(f'/api/servers/survival/restarts/{row["id"]}')
    restarts.count_down(row, no_waiting['now'] + restarts.LEAD)
    assert docker.actions == []


def test_an_unanswered_warning_still_restarts(admin, survival, docker, no_waiting, monkeypatch):
    servers.start_server('survival')
    docker.set_state('mcpanel-survival', 'running', health='healthy')
    row = countdown_row(admin)
    docker.actions.clear()
    monkeypatch.setattr(restarts.rcon, 'address', lambda server_id: ('127.0.0.1', 1))  # nothing listens
    restarts.count_down(row, no_waiting['now'] + restarts.LEAD)
    assert [action for action, _, _ in docker.actions] == ['restart']


def test_a_server_gone_by_its_restart_is_logged(admin, survival, docker, no_waiting, caplog):
    row = countdown_row(admin)
    docker.reachable = False
    restarts.count_down(row, no_waiting['now'] + restarts.LEAD)
    assert "Couldn't restart server survival on schedule: Docker is unreachable" in caplog.text
    assert 'survival' not in restarts.counting_down
