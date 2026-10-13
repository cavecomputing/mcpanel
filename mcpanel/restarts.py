"""Scheduled restarts: cron lines kept per server, each read in the time zone of whoever set it, and
the thread that runs them. Players are warned in game 5 minutes, 1 minute and 10 seconds ahead, then
the server is restarted as Restart does; a server that isn't running then is left alone.

The clock is each time zone's wall clock, minute by minute, as cron (cronie) keeps it. When it
jumps forward for daylight saving, a restart set in the skipped hour happens right after the jump,
and when it goes back, one in the repeated hour doesn't happen twice; a line that runs every so often
(* in its minute or hour) just carries on. A jump of more than 3 hours is the clock being corrected,
and catches nothing up. Nothing is caught up after the panel itself was down either.
"""
import logging
import threading
import time
import zoneinfo
from datetime import datetime, timedelta
from functools import cache

from flask import abort
from werkzeug.exceptions import HTTPException

from . import cron, logs, rcon, servers
from .db import get_db

logger = logging.getLogger(__name__)

WARNINGS = ((300, '5 minutes'), (60, '1 minute'), (10, '10 seconds'))  # seconds before, said in game
LEAD = WARNINGS[0][0]  # a restart's countdown starts this long before its time
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


def restart_dict(row):
    """What the UI shows of a scheduled restart, with when it next happens (Unix seconds)."""
    zone = zoneinfo.ZoneInfo(row['timezone'])
    upcoming = cron.parse(row['cron']).next_after(wall_clock(time.time() + LEAD, row['timezone']))
    return {'id': row['id'], 'cron': row['cron'], 'timezone': row['timezone'],
            'next': upcoming and int(upcoming.replace(tzinfo=zone).timestamp())}


def for_server(server_id):
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM scheduled_restarts WHERE server = ? ORDER BY id', (server_id,)).fetchall()
    return [restart_dict(row) for row in rows]


def add(server_id, line, zone):
    """Schedule restarts of a server by a cron line in a time zone; its dict. 400 says what is wrong."""
    try:
        parsed = cron.parse(line)
    except ValueError as e:
        abort(400, str(e))
    if not isinstance(zone, str) or zone not in zones():
        abort(400, 'Unknown time zone')
    if parsed.next_after(wall_clock(time.time(), zone)) is None:
        abort(400, "That day never comes round")
    with get_db() as conn:
        restart_id = conn.execute('INSERT INTO scheduled_restarts (server, cron, timezone, created) VALUES (?, ?, ?, ?)',
                                  (server_id, ' '.join(line.split()), zone, time.time())).lastrowid
        conn.commit()
        return restart_dict(conn.execute('SELECT * FROM scheduled_restarts WHERE id = ?', (restart_id,)).fetchone())


def remove(server_id, restart_id):
    """Unschedule one of a server's restarts; its cron line, or a 404."""
    with get_db() as conn:
        row = conn.execute('DELETE FROM scheduled_restarts WHERE id = ? AND server = ? RETURNING cron',
                           (restart_id, server_id)).fetchone()
        conn.commit()
    if row is None:
        abort(404, 'No such scheduled restart')
    return row['cron']


def due(rows, virtual, clock):
    """The rows to start counting down for as a time zone's wall clock moves from virtual, the minute
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
    """Handle the minute LEAD seconds from now in every time zone a restart is set in. clocks keeps,
    per time zone, (the minute last handled, the minute the clock last said)."""
    with get_db() as conn:
        rows = conn.execute('SELECT * FROM scheduled_restarts ORDER BY id').fetchall()
    for zone in {row['timezone'] for row in rows}:
        clock = wall_clock(now + LEAD, zone)
        virtual, seen = clocks.get(zone, (clock - MINUTE, None))  # one new to the panel starts now
        if clock == seen:
            continue
        starting, virtual = due([row for row in rows if row['timezone'] == zone], virtual, clock)
        clocks[zone] = (virtual, clock)
        for row in starting:
            with counting_lock:
                if row['server'] in counting_down:
                    continue
                counting_down.add(row['server'])
            threading.Thread(target=count_down, args=(dict(row), now + LEAD), daemon=True).start()


def count_down(row, at):
    """Warn a server's players ahead of a scheduled restart at time at, then restart it, unless the
    schedule is gone or the server isn't running by then."""
    server_id = row['server']
    try:
        for before, words in WARNINGS:
            time.sleep(max(0, at - before - time.time()))
            with get_db() as conn:
                if not conn.execute('SELECT 1 FROM scheduled_restarts WHERE id = ?', (row['id'],)).fetchone():
                    return
            if servers.get_server(server_id)['status'] == 'running':
                try:
                    rcon.run(server_id, servers.rcon_password(server_id), f'say This server restarts in {words}.')
                except HTTPException:
                    pass  # it restarts all the same
        time.sleep(max(0, at - time.time()))
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


def run():
    clocks = {}
    while True:
        time.sleep(61 - time.time() % 60)  # a second past each minute
        try:
            tick(time.time(), clocks)
        except Exception:  # a bad minute (a locked database) mustn't end the schedules for good
            logger.exception('Scheduled restarts failed this minute')


def start_scheduler():
    """before_request: start the thread on the first request, so flask's CLI commands and the
    development server's file watcher, which serve none, don't run schedules too."""
    global scheduler
    with scheduler_lock:
        if scheduler is None:
            scheduler = threading.Thread(target=run, name='scheduled-restarts', daemon=True)
            scheduler.start()
