# Data and backups

## The data folder

Everything mcpanel keeps is in `MCPANEL_DATA_DIR` (`./data` when you run it without Docker):

```
/srv/mcpanel/            # MCPANEL_DATA_DIR
├── mcpanel.db           # SQLite: users, sessions, recovery codes, server access, schedules, who banned whom
├── logs/
│   ├── mcpanel.log      # everything on stdout, rotated
│   └── audit.log        # who did what, rotated
├── servers/
│   └── <server id>/     # one server's /data, bind-mounted into its container
└── backups/
    └── <server id>/     # its backups, <UTC time>-manual.tar.gz or <UTC time>-schedule-<n>.tar.gz
```

The database holds only what Docker doesn't know: accounts, sessions, recovery codes, which member
may use which server, the restarts and backups scheduled for each, and which panel user made each ban (the
ban itself, with its reason, is in the server's own `banned-players.json`). A server's state, port, type, version, memory and CPU live on its
container, and the panel reads them from Docker every time. Back up the whole data folder and you
have every world and every account.

## Mods and plugins

Upload them on a server's Files tab, into `mods/` or `plugins/`. A modpack or a world from elsewhere
can go up as one `.zip`: upload it, then press its Extract button, and what's in it lands in the
folder the zip is in, replacing files of the same names. Or put them in `servers/<server id>/mods/`
or `plugins/` yourself, owned by `PUID`:`PGID`. A new server is created stopped, so they can go in
before its first start.

## Backups

A backup is a `.tar.gz` of the server's whole folder, made when someone presses Back up now on its
Backups tab, or at the times set on its Schedule tab (see
[Schedules](1-getting-started.md#schedules)). Backups made with Back up now are kept until someone
deletes them. Each scheduled line keeps only its newest few and deletes its own older ones.

The tab lists them all together, newest first. Each shows when it was taken in ISO 8601, in your
browser's time zone with its offset, such as `2026-10-13T22:30:00-05:00`, and one a schedule made
is marked with that line's name (or its cron line when it has none). A backup whose line has been
removed is marked Removed schedule; nothing deletes those but you.

A backup's file name says the same, so it still makes sense once downloaded or copied elsewhere:
the time in UTC in ISO 8601's basic format, which has no colons for file systems to trip on, then how
it was made. `20261014T033000Z-manual.tar.gz` is one made with Back up now, and
`20261014T080000Z-schedule-3.tar.gz` one made by schedule line 3. A download is named after the
server too, `survival-20261014T033000Z-manual.tar.gz`. Backups from before 14 October 2026 keep
their old names (`2026-10-13_223000.tar.gz`, `2026-10-13_080000-schedule-3.tar.gz`); the panel
lists, restores and prunes them as before.

- **Making one** works whether the server runs or not. A running server is told to save the world and
  stop writing it (`save-off`) until the backup is done, then to carry on.
- **Restoring** needs the server stopped. The backup is unpacked beside the folder and then swapped
  in, and the folder it replaces is deleted. So the question before it has a box, ticked to start
  with, that backs up the folder as it is first.
- **Downloading** gives you the file as it is.
- One backup or restore runs at a time per server.
- A server's backups are deleted with the server.

The RCON password the image writes into the server's folder never leaves the panel: the `.rcon-cli`
files are left out of backups, and `server.properties` goes into a backup (or a download from Files)
with the password masked and gets the real one back when it is restored or saved.

## Logs

The panel logs JSON lines on stdout (plain lines in a terminal), and the same lines go to
`logs/mcpanel.log`. `logs/audit.log` records sign-ins, failed tries, lockouts, account changes and
every server action, with who did it and from which address. Both rotate at 10 MB and keep 7 gzipped
files. There are no logging settings, and no password, code, token or RCON password is ever logged.

Each server's own output is its Console tab, kept by Docker at up to 3 files of 10 MB, of which
the tab shows the last 2000 lines. The server also keeps its own log in its folder, in the Files tab:
`logs/latest.log` since it last started, and each earlier start's in `logs/` as a `.log.gz`.
