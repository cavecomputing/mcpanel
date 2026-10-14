"""Scheduled restarts and backups: cron lines kept per server, each read in the time zone of whoever
set it, and the thread that runs them. Before a restart, players are warned in game 5 minutes,
1 minute and 10 seconds ahead, then the server is restarted as Restart does, unless it isn't running
by then. A backup is made as Back up now makes it, running or not, and then the schedule's own
backups past the newest few it keeps are deleted; backups made any other way are never touched.

The clock is each time zone's wall clock, minute by minute, as cron (cronie) keeps it. When it
jumps forward for daylight saving, a time set in the skipped hour comes right after the jump, and
when it goes back, one in the repeated hour doesn't come twice; a line that runs every so often (*
in its minute or hour) just carries on. A jump of more than 3 hours is the clock being corrected,
and catches nothing up. Nothing is caught up after the panel itself was down either.
"""
import logging
import threading
import time
import zoneinfo
from contextlib import suppress
from datetime import datetime, timedelta
from functools import cache

from flask import abort
from werkzeug.exceptions import HTTPException

from . import backups, cron, logs, rcon, servers
from .db import get_db

logger = logging.getLogger(__name__)

WARNINGS = ((300, '5 minutes'), (60, '1 minute'), (10, '10 seconds'))  # seconds before a restart, said in game
LEADS = {'restart': WARNINGS[0][0], 'backup': 0}  # what a schedule does -> how long before its time it starts
MAX_KEEP = 1000  # backups a backup schedule may keep
MAX_NAME = 60  # characters in a schedule's name
MAX_JUMP = 180  # minutes the clock may jump for daylight saving; more is a correction
MINUTE = timedelta(minutes=1)

scheduler = None  # the thread, once the first request starts it
scheduler_lock = threading.Lock()
counting_down = set()  # ids of servers with a restart's countdown running, so two never overlap
counting_lock = threading.Lock()


@cache
def zones():
    return zoneinfo.available_timezones()


def wall_clock(seconds, zone):
    """A Unix time as a naive wall-clock minute in an IANA time zone."""
    return datetime.fromtimestamp(seconds, zoneinfo.ZoneInfo(zone)).replace(tzinfo=None, second=0, microsecond=0)


def schedule_dict(row):
    """What the UI shows of a schedule, with when it next comes (Unix seconds)."""
    zone = zoneinfo.ZoneInfo(row['timezone'])
    upcoming = cron.parse(row['cron']).next_after(wall_clock(time.time() + LEADS[row['action']], row['timezone']))
    return {'id': row['id'], 'name': row['name'], 'action': row['action'], 'cron': row['cron'], 'timezone': row['timezone'],
            'keep': row['keep'], 'next': upcoming and int(upcoming.replace(tzinfo=zone).timestamp())}


def for_server(server_id):
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM schedules WHERE server = ? ORDER BY id', (server_id,)).fetchall()
    return [schedule_dict(row) for row in rows]


def checked_name(name):
    """A schedule's name, its spaces tidied, or None for none; 400 if it can't be one."""
    if name is not None and not isinstance(name, str):
        abort(400, 'A name is text')
    name = ' '.join((name or '').split())
    if len(name) > MAX_NAME:
        abort(400, f'A name has at most {MAX_NAME} characters')
    return name or None


def add(server_id, action, line, zone, keep, name=None):
    """Schedule a server's restarts or backups by a cron line in a time zone, a backup schedule
    keeping its newest keep backups, called name if given; its dict. 400 says what is wrong."""
    name = checked_name(name)
    if action not in LEADS:
        abort(400, 'A schedule restarts the server or backs it up')
    if action == 'restart':
        keep = None
    elif not (type(keep) is int and 1 <= keep <= MAX_KEEP):
        abort(400, f'Keep from 1 to {MAX_KEEP} backups')
    try:
        parsed = cron.parse(line)
    except ValueError as e:
        abort(400, str(e))
    if not isinstance(zone, str) or zone not in zones():
        abort(400, 'Unknown time zone')
    if parsed.next_after(wall_clock(time.time(), zone)) is None:
        abort(400, "That day never comes round")
    with get_db() as conn:
        schedule_id = conn.execute('INSERT INTO schedules (server, action, cron, timezone, keep, created, name) VALUES (?, ?, ?, ?, ?, ?, ?)',
                                   (server_id, action, ' '.join(line.split()), zone, keep, time.time(), name)).lastrowid
        conn.commit()
        return schedule_dict(conn.execute('SELECT * FROM schedules WHERE id = ?', (schedule_id,)).fetchone())


def rename(server_id, schedule_id, name):
    """Call one of a server's schedules name (None or '' for no name); its dict, or a 404."""
    name = checked_name(name)
    with get_db() as conn:
        row = conn.execute('UPDATE schedules SET name = ? WHERE id = ? AND server = ? RETURNING *', (name, schedule_id, server_id)).fetchone()
        conn.commit()
    if row is None:
        abort(404, 'No such schedule')
    return schedule_dict(row)


def remove(server_id, schedule_id):
    """Remove one of a server's schedules; its dict, or a 404."""
    with get_db() as conn:
        row = conn.execute('DELETE FROM schedules WHERE id = ? AND server = ? RETURNING *', (schedule_id, server_id)).fetchone()
        conn.commit()
    if row is None:
        abort(404, 'No such schedule')
    return dict(row)


def due(rows, virtual, clock):
    """The rows to start as a time zone's wall clock moves from virtual, the minute
    last handled, to clock; and the new virtual minute. cronie's rules (cron.c): a minute or a few
    late, each missed minute's; a jump forward for daylight saving, the skipped minutes' fixed times
    but only the new minute's wildcards; back, only the wildcards, until the clock catches up."""
    diff = (clock - virtual) // MINUTE
    lines = [(row, cron.parse(row['cron'])) for row in rows]
    if 0 < diff <= MAX_JUMP:
        missed = [virtual + MINUTE * n for n in range(1, diff + 1)]
        return [row for row, line in lines
                if (line.matches(clock) if line.wildcard and diff > 5 else any(map(line.matches, missed)))], clock
    if -MAX_JUMP <= diff <= 0:
        return [row for row, line in lines if line.wildcard and line.matches(clock)], virtual
    return [row for row, line in lines if line.matches(clock)], clock


def tick(now, clocks):
    """Start what is due: for each time zone a schedule is set in and each thing schedules do, the
    minute that thing's lead from now. clocks keeps, per (time zone, action), (the minute last
    handled, the minute the clock last said)."""
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM schedules ORDER BY id').fetchall()
    for zone, action in {(row['timezone'], row['action']) for row in rows}:
        at = now + LEADS[action]
        clock = wall_clock(at, zone)
        virtual, seen = clocks.get((zone, action), (clock - MINUTE, None))  # one new to the panel starts now
        if clock == seen:
            continue
        starting, virtual = due([row for row in rows if (row['timezone'], row['action']) == (zone, action)], virtual, clock)
        clocks[zone, action] = (virtual, clock)
        for row in starting:
            if action == 'backup':
                threading.Thread(target=back_up, args=(dict(row),), daemon=True).start()
                continue
            with counting_lock:
                if row['server'] in counting_down:
                    continue
                counting_down.add(row['server'])
            threading.Thread(target=count_down, args=(dict(row), at), daemon=True).start()


def warn(server_id, verb, at, warnings, called_off=lambda: False):
    """Tell a server's players in game that it {verb} at time at, at each of warnings' (seconds
    before, words) while it runs, then wait for at: True then, False as soon as called_off() says so.
    A Stop or Restart that warns players first waits here too."""
    for before, words in warnings:
        time.sleep(max(0, at - before - time.time()))
        if called_off():
            return False
        if servers.get_server(server_id)['status'] == 'running':
            with suppress(HTTPException):  # it goes ahead all the same
                rcon.run(server_id, servers.rcon_password(server_id), f'say This server {verb} in {words}.')
    time.sleep(max(0, at - time.time()))
    return True


def count_down(row, at):
    """Warn a server's players ahead of a scheduled restart at time at, then restart it, unless the
    schedule is gone or the server isn't running by then."""
    server_id = row['server']

    def unscheduled():
        with get_db() as conn:
            return not conn.execute('SELECT 1 FROM schedules WHERE id = ?', (row['id'],)).fetchone()

    try:
        if not warn(server_id, 'restarts', at, WARNINGS, unscheduled):
            return
        server = servers.get_server(server_id)
        if server['status'] not in ('running', 'unresponsive'):
            logger.info('Left server %s alone at its scheduled restart: it is %s', server_id, server['status'],
                        extra={'server': server_id})
            return
        servers.restart_server(server_id)
        logs.audit('server_restarted', server=server_id, server_name=server['name'], schedule=row['cron'])
    except HTTPException as e:  # the server deleted meanwhile, Docker unreachable or refusing
        logger.warning("Couldn't restart server %s on schedule: %s", server_id, e.description, extra={'server': server_id})
    finally:
        with counting_lock:
            counting_down.discard(server_id)


def back_up(row):
    """Make a scheduled backup of a server, as Back up now does, then delete the schedule's own
    backups past the newest it keeps."""
    server_id = row['server']
    try:
        server = servers.get_server(server_id)
        backup = backups.create(server_id, server['status'] not in ('stopped', 'crashed'), row['id'])
        logs.audit('backup_created', server=server_id, server_name=server['name'], backup=backup['name'],
                   schedule=row['cron'])
        own = [made for made in backups.list_backups(server_id) if made['schedule'] == row['id']]  # newest first
        for old in own[row['keep']:]:
            backups.delete(server_id, old['name'])
            logs.audit('backup_deleted', server=server_id, server_name=server['name'], backup=old['name'],
                       schedule=row['cron'])
    except HTTPException as e:  # one under way already, a full disk, the server deleted, Docker unreachable
        logger.warning("Couldn't back up server %s on schedule: %s", server_id, e.description, extra={'server': server_id})


def run():
    clocks = {}
    while True:
        time.sleep(61 - time.time() % 60)  # a second past each minute
        try:
            tick(time.time(), clocks)
        except Exception:  # a bad minute (a locked database) mustn't end the schedules for good
            logger.exception('Schedules failed this minute')


def start_scheduler():
    """before_request: start the thread on the first request, so flask's CLI commands and the
    development server's file watcher, which serve none, don't run schedules too."""
    global scheduler
    with scheduler_lock:
        if scheduler is None:
            scheduler = threading.Thread(target=run, name='schedules', daemon=True)
            scheduler.start()
