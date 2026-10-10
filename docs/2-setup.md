# Setup

Run the commands on this page from the top folder of the mcpanel repository (the one that contains
`app.py`).

## What you need

- A Linux machine or VM with Docker Engine and the Compose plugin
- [Caddy](https://caddyserver.com/) (or another reverse proxy) for HTTPS, and a DNS name for the panel
- A DNS name for players, pointing at your public address, and a router that forwards a port range

mcpanel is meant to sit on the internet behind Caddy, and the game ports are open to the internet too,
so give the machine a network of its own. A VM on its own VLAN, for example on Proxmox, keeps the
servers away from the rest of your network.

## Settings

Put them in `docker/.env`:

```bash
MCPANEL_PUBLIC_HOST=mc.example.com
MCPANEL_PORT_RANGE=25565-25600
MCPANEL_DATA_DIR=/srv/mcpanel
PUID=1000
PGID=1000
```

| Setting | What it is |
|---|---|
| `MCPANEL_PUBLIC_HOST` | The name players type, shown as each server's address. Required. |
| `MCPANEL_PORT_RANGE` | The host ports the panel may give to servers, one each, such as `25565-25600`. Required. |
| `MCPANEL_DATA_DIR` | An absolute path on the host for everything the panel keeps. The container mounts it at the same path, because each server's folder in it is bind-mounted into that server's container. |
| `PUID`, `PGID` | Who owns the data folder. The panel and every server run as them. Default 1000. |

That is the whole configuration. Logging has fixed defaults and no settings.

## Start

```bash
docker compose -f docker/compose.yml up --build -d
```

The panel listens on `127.0.0.1:5000`, for Caddy on the same machine. Servers keep running when the
panel stops, restarts or crashes, and it finds them again when it comes back.

## The first admin

```bash
docker compose -f docker/compose.yml exec -u 1000:1000 mcpanel flask --app app create-user matt --admin
```

Use your `PUID:PGID` after `-u`, so the files the command writes stay the panel's. It prints a
one-time password, good for 24 hours. Sign in with it on the panel's address and follow
[Signing in for the first time](1-getting-started.md#signing-in-for-the-first-time). Add everyone else
from the Users page. Leave out `--admin` to make a member.

### When every admin is locked out

```bash
docker compose -f docker/compose.yml exec -u 1000:1000 mcpanel flask --app app reset-user matt
```

It prints a new one-time password for that account and drops its two-factor set-up, recovery codes
and sessions, so its owner sets them up again on the next sign-in.

## Caddy

```
panel.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

Caddy handles HTTPS and tells the panel the browser used it, so the sign-in cookie is marked Secure.
If Caddy runs on another machine, change the `ports` line in `docker/compose.yml` to an address Caddy
can reach, never one strangers can.

## The router

Forward the whole `MCPANEL_PORT_RANGE` (TCP) to the machine. Players connect to `mc.example.com` for
the server on 25565 and to `mc.example.com:25566` and so on for the others. Only game ports are
published: RCON stays on the private `mcpanel` Docker network, and there are no second ports for voice
chat, Bedrock or query.

If something outside Docker already holds a port in the range, Docker refuses it and the panel says
so when you create the server.

## Updating

```bash
git pull
docker compose -f docker/compose.yml up --build -d
```

Servers keep running while the panel restarts. A server keeps the server image it was made with;
creating a server always pulls the newest copy of its Java tag first.

## Logs

```bash
docker compose -f docker/compose.yml logs -f
```

See [Data and backups](3-data-and-backups.md#logs) for the log files.
