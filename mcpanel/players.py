"""Who is on a server, and its whitelist, operators and bans.

Who is online comes from the list command over RCON. The lists are the server's own JSON files in its
folder, which it rewrites as soon as a command changes them; changes go through the console
(whitelist add, op, ban, pardon...), so the server is the one that writes them.
"""
import json
import re

from werkzeug.exceptions import BadGateway, HTTPException

from . import files, rcon, servers

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
