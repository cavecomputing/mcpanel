"""Runtime configuration, read once from the environment at import time.

Other modules read these as `config.NAME` when they need them, never `from .config import NAME`,
so the tests can point them at a temporary directory.
"""
import os
from pathlib import Path

# Resolved so paths never depend on the working directory. Inside Docker this must be the same path
# as on the host: servers' data folders are bind mounts, and Docker reads those paths on the host.
DATA_DIR = Path(os.getenv('MCPANEL_DATA_DIR', 'data')).resolve()
DATABASE = DATA_DIR / 'mcpanel.db'
LOGS_DIR = DATA_DIR / 'logs'
SERVERS_DIR = DATA_DIR / 'servers'

# The name players type to reach a server, e.g. mc.example.com. create_app() refuses to start without it.
PUBLIC_HOST = os.getenv('MCPANEL_PUBLIC_HOST', '').strip()

# The host ports the panel may publish, one per server, e.g. "25565-25600". The router forwards the same range.
PORT_RANGE = os.getenv('MCPANEL_PORT_RANGE', '').strip()


def port_range():
    """PORT_RANGE as (first, last), or a ValueError saying what is wrong with it."""
    first, sep, last = PORT_RANGE.partition('-')
    if not sep or not first.strip().isdigit() or not last.strip().isdigit():
        raise ValueError(f'MCPANEL_PORT_RANGE must look like 25565-25600, not "{PORT_RANGE}"')
    first, last = int(first), int(last)
    if not 1024 <= first <= last <= 65535:
        raise ValueError(f'MCPANEL_PORT_RANGE must be two ports from 1024 to 65535, lowest first, not "{PORT_RANGE}"')
    return first, last
