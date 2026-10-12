"""Who is on a server, and its whitelist, operators and bans.

Who is online comes from the list command over RCON. The lists are the server's own JSON files in its
folder, which it rewrites as soon as a command changes them. While it runs, changes go through the
console (whitelist add, op, ban, pardon...), so the server is the one that writes them. While it is
stopped, change_saved() writes the file as the command would, and the server reads it when it starts.
"""
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

from flask import abort
from werkzeug.exceptions import BadGateway, HTTPException

from . import files, properties, rcon, servers

NAME = re.compile(r'[A-Za-z0-9_]{1,16}')
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
    """{online, max, whitelist, ops, banned}; online and max are None when the server can't say."""
    names, most = (online(server_id) if running else None) or (None, None)
    return {
        'online': names, 'max': most,
        'whitelist': sorted((entry['name'] for entry in saved_list(server_id, 'whitelist.json')), key=str.casefold),
        'ops': sorted((entry['name'] for entry in saved_list(server_id, 'ops.json')), key=str.casefold),
        'banned': sorted(({'name': entry['name'], 'reason': str(entry.get('reason', ''))}
                          for entry in saved_list(server_id, 'banned-players.json')), key=lambda e: e['name'].casefold()),
    }


def change_saved(server_id, command, name):
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
            entry |= {'created': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S +0000'), 'source': 'mcpanel',
                      'expires': 'forever', 'reason': 'Banned by an operator.'}
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
    for line in properties.lines_of(server_id):
        match = properties.KEY.fullmatch(line)
        if match and not line.lstrip().startswith(('#', '!')) and properties.unescape(match[1]) == 'online-mode':
            return properties.parsed('online-mode', properties.unescape(match[2]))
    return True
