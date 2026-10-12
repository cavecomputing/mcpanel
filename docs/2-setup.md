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
MCPANEL_GAME_HOST=mc.example.com
MCPANEL_ALLOWED_HOSTS=panel.example.com
MCPANEL_PORT_RANGE=25565-25600
MCPANEL_DATA_DIR=/srv/mcpanel
# MCPANEL_MFA_ENFORCEMENT=off
PUID=1000
PGID=1000
```

| Setting | What it is |
|---|---|
| `MCPANEL_GAME_HOST` | The name players type, shown as each server's address. Required. |
| `MCPANEL_ALLOWED_HOSTS` | Where the panel's own pages are: the name in your Caddy file, such as `panel.example.com`, or an IP address, or several separated by commas (`panel.example.com,10.0.0.5`). A port after one is ignored. Requests for any other name or address are refused with a 400. Leave it out and the panel answers to every one, and logs a warning on every start. See [Caddy](#caddy). |
| `MCPANEL_PORT_RANGE` | The host ports the panel may give to servers, one each, such as `25565-25600`. Required. |
| `MCPANEL_DATA_DIR` | An absolute path on the host for everything the panel keeps. The container mounts it at the same path, because each server's folder in it is bind-mounted into that server's container. |
| `MCPANEL_MFA_ENFORCEMENT` | `on` (the default) or `off`. Off, signing in takes only the password, with no code from an authenticator app. See [Two-factor sign-in](#two-factor-sign-in). `MFA_ENFORCEMENT` works too. |
| `PUID`, `PGID` | Who owns the data folder. The panel and every server run as them. Default 1000. |

That is the whole configuration. Logging has fixed defaults and no settings.

## Start

```bash
docker compose -f docker/compose.yml up --build -d
```

The panel listens on `127.0.0.1:5000`, for Caddy on the same machine. Servers keep running when the
panel stops, restarts or crashes, and it finds them again when it comes back.

The panel and the servers share the `mcpanel` Docker network, which the panel's console, player
lists and PvP setting reach each server over. If you edit `compose.yml` to give the service other
networks, keep `default` in its `networks:` list, or the console says the panel can't find the server
on the `mcpanel` network.

## Two-factor sign-in

Every account signs in with its password and then a code from an authenticator app, unless
`MCPANEL_MFA_ENFORCEMENT=off` is in `docker/.env`. To switch it either way, change that line and run
`docker compose -f docker/compose.yml up -d`. Nobody is signed out, and nobody's sign-in breaks:

- **Switched off**, everyone signs in with their password alone. Anyone who had two-factor sign-in
  keeps it set up, unused. A new account chooses its password on its first sign-in and that's all.
- **Switched back on**, those who had it set up are asked for codes again, from the same app, and
  their recovery codes work again. Anyone who never set it up (an account made while it was off) is
  shown the QR code after their password on their next sign-in, and has to finish that set-up to get
  in.

Until they have, their password alone is what sets up their two-factor sign-in, so if a password may
have leaked, reset that account from the Users page rather than waiting for them.

The panel refuses to start with a value other than on or off, and logs a warning on every start
while it is off.

## The platform admin

While the panel has no accounts, opening it in a browser asks for the platform admin's username, then
its password and (unless it is switched off) two-factor sign-in, the same set-up as in
[Signing in for the first time](1-getting-started.md#signing-in-for-the-first-time). The account is
made when that set-up is done, and from then on the page is gone. Add everyone else from the Users
page.

Whoever gets there first becomes the platform admin, so do it as soon as Caddy points at the panel,
before you tell anyone its address. If you would rather not, make it from the command line before
Caddy is set up:

```bash
docker compose -f docker/compose.yml exec -u 1000:1000 mcpanel flask --app app create-user matt --platform-admin
```

Use your `PUID:PGID` after `-u`, so the files the command writes stay the panel's. It prints a
one-time password, good for 24 hours, to sign in with. Use `--admin` instead for an admin, or neither
for a member. A panel whose accounts include an admin but no platform admin makes its earliest admin
the platform admin when it starts.

### When every platform admin is locked out

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
It passes on the name the browser asked for, which the panel checks against
`MCPANEL_ALLOWED_HOSTS`, so that name must be in it. Don't add `header_up Host` to the Caddy file:
with the name changed, the panel refuses every page. `/healthz` answers on any name, for the container's own health check.

To reach the panel without Caddy as well, by its address on your network say, add that address:
`MCPANEL_ALLOWED_HOSTS=panel.example.com,10.0.0.5`.
If Caddy runs on another machine, change the `ports` line in `docker/compose.yml` to an address Caddy
can reach, never one strangers can.

## The router

Forward the whole `MCPANEL_PORT_RANGE` (TCP) to the machine. Players connect to `mc.example.com` for
the server on 25565 and to `mc.example.com:25566` and so on for the others. Only game ports are
published: RCON stays on the private `mcpanel` Docker network, and there are no second ports for voice
chat, Bedrock or query.

If something outside Docker already holds a port in the range, Docker refuses it and the panel says
so when you start the server that was given it. Free that port, or leave it out of the range.

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
