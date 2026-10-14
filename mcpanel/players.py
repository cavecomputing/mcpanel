"""Who is on a server, everyone who has ever joined it, and its whitelist, operators and bans.

Who is online comes from the list command over RCON. Everyone who has joined comes from the world's
player files, named by their UUIDs, with names from the server's user cache. The lists are the
server's own JSON files in its folder, which it rewrites as soon as a command changes them. While it
runs, changes are console commands (whitelist add, op, ban, pardon...), so the server is the one that
writes them. While it is stopped, change_saved() writes the file as the command would, and the
server reads it when it starts. A ban's reason is kept in the file, where the game shows it to the
player; who in the panel made it is noted in the database (bans), since the file says only "Rcon".
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import abort
from werkzeug.exceptions import BadGateway, HTTPException

from . import files, properties, rcon, servers
from .db import get_db

NAME = re.compile(r'[A-Za-z0-9_]{1,16}')
MAX_REASON = 200
BAN_TIME = '%Y-%m-%d %H:%M:%S %z'  # a ban's "created", as the server writes it
NOTED_WITHIN = 120  # seconds between the panel noting a ban and the server's time on it, for the note to be that ban's
PROFILE = 'https://api.mojang.com/users/profiles/minecraft/{}'  # a Java account's name -> its id
# The console command each change stands for -> (its list's file, whether it adds the player, what's said after)
CHANGES = {
    'whitelist add': ('whitelist.json', True, 'Added {} to the whitelist'),
    'whitelist remove': ('whitelist.json', False, 'Removed {} from the whitelist'),
    'op': ('ops.json', True, 'Made {} an operator'),
    'deop': ('ops.json', False, '{} is no longer an operator'),
    'ban': ('banned-players.json', True, 'Banned {}'),
    'pardon': ('banned-players.json', False, 'Pardoned {}'),
}

# "There are 2 of a max of 20 players online: Steve, Alex"; before 1.13, "There are 2/20 players online:"
LIST = re.compile(r'There are (\d+)(?: of a max of |/)(\d+) players online:(.*)', re.DOTALL)


def online(server_id):
    """(the names of the players online, the most it takes), or None when it can't say: stopped,
    still starting, or a list it words differently."""
    try:
        answer = rcon.run(server_id, servers.rcon_password(server_id), 'list')
    except BadGateway:
        return None
    match = LIST.search(answer)
    if not match:
        return None
    return [name.strip() for name in match[3].split(',') if name.strip()], int(match[2])


def saved_list(server_id, name):
    """One of the server's JSON lists, [] when it has none yet or it doesn't parse."""
    try:
        entries = json.loads(files.read_bytes(server_id, name, files.MAX_EDIT))
    except (HTTPException, ValueError):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get('name'), str)] \
        if isinstance(entries, list) else []


def players(server_id, running):
    """{online, max, all, whitelist, ops, banned}; online and max are None when the server can't say."""
    names, most = (online(server_id) if running else None) or (None, None)
    everyone = joined(server_id)
    seen = {player['name'].casefold() for player in everyone}
    return {
        'online': names, 'max': most,
        # someone on for the first time has no player file until the server next saves
        'all': [{'name': name, 'last_on': None} for name in names or () if name.casefold() not in seen] + everyone,
        'whitelist': sorted((entry['name'] for entry in saved_list(server_id, 'whitelist.json')), key=str.casefold),
        'ops': sorted((entry['name'] for entry in saved_list(server_id, 'ops.json')), key=str.casefold),
        'banned': banned(server_id),
    }


def joined(server_id):
    """[{name, last_on}] of everyone who has joined the server, the latest first: a file per player
    in its world's playerdata, which the server saves as they leave and every few minutes while
    they're on, so its time is when they were last on. A player whose name the server no longer has
    is left out."""
    try:
        entries = files.list_dir(server_id, f'{saved_property(server_id, "level-name", "world")}/playerdata')
    except HTTPException:  # no world yet
        return []
    names = {}
    for file in ('usercache.json', 'whitelist.json', 'ops.json', 'banned-players.json'):
        for entry in saved_list(server_id, file):
            names.setdefault(str(entry.get('uuid', '')).lower(), entry['name'])
    everyone = [{'name': names[entry['name'][:-4].lower()], 'last_on': entry['modified']} for entry in entries
                if entry['type'] == 'file' and entry['name'].endswith('.dat') and entry['name'][:-4].lower() in names]
    return sorted(everyone, key=lambda player: -player['last_on'])


def banned(server_id):
    """[{name, reason, created, by}] of the server's bans: created in Unix seconds (None when the file's
    time doesn't parse), by the panel user who made the ban, else whoever the file says did."""
    with get_db() as conn:
        notes = conn.execute('SELECT player, banned_by, created FROM bans WHERE server = ?', (server_id,)).fetchall()
    bans = []
    for entry in saved_list(server_id, 'banned-players.json'):
        try:
            created = datetime.strptime(str(entry.get('created')), BAN_TIME).timestamp()
        except ValueError:
            created = None
        by = next((note['banned_by'] for note in notes if created and note['player'].casefold() == entry['name'].casefold()
                   and abs(note['created'] - created) <= NOTED_WITHIN), str(entry.get('source', '')))
        bans.append({'name': entry['name'], 'reason': str(entry.get('reason', '')), 'created': created, 'by': by})
    return sorted(bans, key=lambda ban: ban['name'].casefold())


def note_ban(server_id, name, username):
    """Note that username banned name, for banned() to match to the ban the server writes."""
    with get_db() as conn:
        conn.execute('INSERT INTO bans (server, player, banned_by, created) VALUES (?, ?, ?, ?)', (server_id, name, username, time.time()))
        conn.commit()


def change_saved(server_id, command, name, reason=''):
    """While the server is stopped, make a CHANGES command's change in its list's file, with an entry
    shaped as the server writes it, and say what was done. A player is matched by name, any case."""
    file, adds, done = CHANGES[command]
    entries = [entry for entry in saved_list(server_id, file) if entry['name'].casefold() != name.casefold()]
    if adds:
        player_id, name = profile(server_id, name)
        entry = {'uuid': str(player_id), 'name': name}
        if file == 'ops.json':
            entry |= {'level': 4, 'bypassesPlayerLimit': False}
        elif file == 'banned-players.json':
            entry |= {'created': datetime.now(timezone.utc).strftime(BAN_TIME), 'source': 'mcpanel',
                      'expires': 'forever', 'reason': reason or 'Banned by an operator.'}
        entries.append(entry)
    files.write(server_id, file, [json.dumps(entries, indent=2).encode()])
    return done.format(name)


def profile(server_id, name):
    """(a player's UUID, their name as their account spells it): the account's from Mojang when the
    server checks accounts (online mode, its default), else the one an offline server gives the
    name, from "OfflinePlayer:<name>" as Java's UUID.nameUUIDFromBytes() makes it."""
    if not online_mode(server_id):
        return uuid.UUID(bytes=hashlib.md5(f'OfflinePlayer:{name}'.encode()).digest(), version=3), name
    try:
        with urllib.request.urlopen(PROFILE.format(urllib.parse.quote(name)), timeout=5) as answer:
            account = json.load(answer) if answer.status == 200 else None
    except urllib.error.HTTPError as e:
        if e.code != 404:
            abort(502, f"Mojang wouldn't look up {name} ({e.code}); try again, or start the server and do it then")
        account = None
    except (OSError, ValueError):
        abort(502, f"Mojang didn't answer, so the panel can't look up {name}; try again, or start the server and do it then")
    if not account:
        abort(404, f'There is no Minecraft account called {name}')
    return uuid.UUID(account['id']), account['name']


def online_mode(server_id):
    """Whether server.properties has the server check accounts with Mojang: yes unless it says not."""
    return properties.parsed('online-mode', saved_property(server_id, 'online-mode', 'true'))


def saved_property(server_id, key, default):
    """A setting's text in server.properties, default when the file leaves it out."""
    for line in properties.lines_of(server_id):
        match = properties.KEY.fullmatch(line)
        if match and not line.lstrip().startswith(('#', '!')) and properties.unescape(match[1]) == key:
            return properties.unescape(match[2])
    return default
