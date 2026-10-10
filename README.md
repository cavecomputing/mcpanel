<p align="center">
  <img src="mcpanel/static/favicon.svg" alt="" width="112">
</p>

<h1 align="center">mcpanel</h1>

<p align="center">
  A small web panel for hosting Minecraft servers on one machine
</p>

mcpanel lets a handful of people run Minecraft servers from a browser. Every server is its own
[`itzg/minecraft-server`](https://github.com/itzg/docker-minecraft-server) container. The panel is
one more container next to them that talks to Docker, the way Portainer does. Servers keep running
when the panel restarts, and the panel finds them again by their Docker labels.

- Create a server (Vanilla, Paper, Purpur, Fabric, Forge, NeoForge or Quilt; a Minecraft version,
  how much memory, and a Java version, which the panel matches to the Minecraft version unless you
  pick one), then start, stop and restart it
- Change a server's game settings (MOTD, difficulty, game mode, view distance, whitelist...), and
  while it's stopped its name, type, version and Java; admins also change its memory and delete it
- A new server stays stopped until you start it, so mods and files can go in its folder first
- Each server's files in the browser: browse, edit text files such as `server.properties`, upload
  mods and plugins, download, rename, delete
- One game port per server, from a range you choose, shown as the address players type
- Admins manage every server and every user; members see and run only the servers an admin gave them
- An admin adds each account and hands over a one-time password, valid for 24 hours; signing in
  with it, the person chooses their own password and sets up two-factor sign-in
- Every account signs in with a password and a TOTP code, with recovery codes for a lost phone, and
  an admin can reset someone's sign-in when they lose both
- See your signed-in devices and sign them out; change your password

## Requirements

- A Linux machine or VM with Docker Engine and the Compose plugin
- [Caddy](https://caddyserver.com/) (or another reverse proxy) for HTTPS, and a DNS name for the panel
- A DNS name for players, pointing at your public address, and a router that forwards a port range

## Quick start

```bash
git clone https://github.com/cavecomputing/mcpanel.git
cd mcpanel
```

Put the settings in `docker/.env`:

```bash
MCPANEL_PUBLIC_HOST=mc.example.com
MCPANEL_PORT_RANGE=25565-25600
MCPANEL_DATA_DIR=/srv/mcpanel
PUID=1000
PGID=1000
```

- `MCPANEL_PUBLIC_HOST`: the name players type, shown as each server's address
- `MCPANEL_PORT_RANGE`: the host ports the panel may give to servers, one each
- `MCPANEL_DATA_DIR`: an absolute path on the host. The container mounts it at the same path,
  because each server's folder in it is bind-mounted into that server's container
- `PUID` and `PGID`: who owns the data folder. The panel and every server run as them (default 1000)

Then start it and create the first admin:

```bash
docker compose -f docker/compose.yml up --build -d
docker compose -f docker/compose.yml exec -u 1000:1000 mcpanel flask --app app create-user matt --admin
```

Use your `PUID:PGID` after `-u`, so the files the command writes stay the panel's. It prints a
one-time password. Within 24 hours, sign in with it on the panel's address
(`https://panel.example.com`), choose a password of at least 12 characters, scan the QR code with an
authenticator app, enter its code, and keep the recovery codes it shows once. Add everyone else from
the Users page, which shows each new account's one-time password once for you to pass on.

If every admin is locked out, run `flask --app app reset-user <name>` the same way: it prints a new
one-time password for that account and drops its two-factor set-up, recovery codes and sessions.

## Caddy and the router

The container listens on `127.0.0.1:5000` only, for Caddy on the same machine:

```
panel.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

If Caddy runs elsewhere, change the `ports` line in `docker/compose.yml` to an address Caddy can
reach. Caddy handles HTTPS and tells the panel the browser used it, so the sign-in cookie is Secure.

On the router, forward the whole `MCPANEL_PORT_RANGE` (TCP) to the machine. Players connect to
`mc.example.com` for a server on 25565, and to `mc.example.com:25566` and so on for the others. RCON
stays on the private `mcpanel` Docker network and is never published.

## Updating

```bash
git pull
docker compose -f docker/compose.yml up --build -d
```

Servers keep running while the panel restarts.

## Your data

```
/srv/mcpanel/            # MCPANEL_DATA_DIR
├── mcpanel.db           # SQLite: users, sessions, recovery codes, server access
├── logs/
│   ├── mcpanel.log      # everything on stdout, rotated
│   └── audit.log        # who did what, rotated
└── servers/
    └── <server id>/     # one server's /data, bind-mounted into its container
```

To add mods or plugins before a new server's first start, upload them on its Files tab, or put them
in `servers/<server id>/mods/` or `plugins/` yourself, owned by `PUID`:`PGID`.

A server's state, port, type, version and memory live on its container, not in the database:
Docker is the source of truth. Back up the data folder and you have every world and every account.

Logs are JSON lines on stdout (`docker compose -f docker/compose.yml logs -f`) and in
`logs/mcpanel.log`. `audit.log` records sign-ins, failed tries, lockouts, account changes and every
server action, with the user and IP. Both rotate at 10 MB and keep 7 gzipped files. There are no
logging settings.

## Security

- The panel holds the Docker socket, which is root on the host. So it only ever runs
  `itzg/minecraft-server` containers, each with one mount (its own folder under `servers/`), never
  privileged, and never with options a client sent.
- Every account has TOTP. Five wrong passwords or codes in a row lock the account for 15 minutes.
- Admins can create servers and accounts: make admins only people you'd trust with the machine.
- The game ports are open to the internet. Keep the machine on its own VLAN, away from the rest of
  your network.

## Development

All you need is [uv](https://docs.astral.sh/uv/), and Docker to do anything with servers. Without a
reachable Docker socket the panel still starts and says Docker is unreachable.

```bash
uv sync
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run app.py --debug
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run flask --app app create-user matt --admin
uv run pytest
```

Data goes in `./data` unless `MCPANEL_DATA_DIR` says otherwise. See [CLAUDE.md](CLAUDE.md) for how
it works and the conventions for changing it.

## License and credit

MIT, see [LICENSE](LICENSE). The servers themselves run on
[itzg/docker-minecraft-server](https://github.com/itzg/docker-minecraft-server), which does the
real work of downloading, configuring and running each server type.
