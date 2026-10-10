<p align="center">
  <img src="mcpanel/static/favicon.svg" alt="" width="112">
</p>

<h1 align="center">mcpanel</h1>

<p align="center">
  A small web panel for hosting Minecraft servers on one machine
</p>

> **Work in progress.** mcpanel is early and still changing. It hasn't yet been run against real
> Minecraft servers on a real host or tested by someone trying to break it, so don't rely on it
> for anything you can't afford to lose, and expect rough edges and changes between updates.

mcpanel lets a handful of people run Minecraft servers from a browser. Every server is its own
[`itzg/minecraft-server`](https://github.com/itzg/docker-minecraft-server) container, and the panel
is one more container next to them that talks to Docker, the way Portainer does. Servers keep running
when the panel restarts.

Create a server, start and stop it, use its console, manage its players, files, mods, backups and
settings, and hand it to the people who should run it. Every account signs in with a password and a
TOTP code, because the panel is meant to be on the internet.

## Quick start

You need a Linux machine with Docker and the Compose plugin, and a reverse proxy such as Caddy for
HTTPS.

```bash
git clone https://github.com/cavecomputing/mcpanel.git
cd mcpanel
```

Put the settings in `docker/.env`:

```bash
MCPANEL_PUBLIC_HOST=mc.example.com
MCPANEL_PORT_RANGE=25565-25600
MCPANEL_DATA_DIR=/srv/mcpanel
```

That is the name players type, the game ports the panel may give out (one per server), and an
absolute path on the host for its data.

Then start it and create the first admin:

```bash
docker compose -f docker/compose.yml up --build -d
docker compose -f docker/compose.yml exec -u 1000:1000 mcpanel flask --app app create-user matt --admin
```

Point Caddy at `127.0.0.1:5000`, forward the port range on your router, and sign in with the
one-time password it printed. [Setup](docs/2-setup.md) has the details.

## Updating

```bash
git pull
docker compose -f docker/compose.yml up --build -d
```

Servers keep running while the panel restarts.

## Documentation

- [Getting started](docs/1-getting-started.md): signing in, accounts, creating a server, its tabs
- [Setup](docs/2-setup.md): settings, the first admin, Caddy, the router, updating
- [Data and backups](docs/3-data-and-backups.md): the data folder, mods, backups, logs
- [Security](docs/4-security.md): sign-in, who may do what, Docker, files
- [Development](docs/5-development.md): running it locally and the tests

## License and credit

MIT, see [LICENSE](LICENSE). The servers themselves run on
[itzg/docker-minecraft-server](https://github.com/itzg/docker-minecraft-server), which does the
real work of downloading, configuring and running each server type.
