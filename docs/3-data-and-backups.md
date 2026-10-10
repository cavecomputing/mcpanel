# Data and backups

## The data folder

Everything mcpanel keeps is in `MCPANEL_DATA_DIR` (`./data` when you run it without Docker):

```
/srv/mcpanel/            # MCPANEL_DATA_DIR
├── mcpanel.db           # SQLite: users, sessions, recovery codes, server access
├── logs/
│   ├── mcpanel.log      # everything on stdout, rotated
│   └── audit.log        # who did what, rotated
├── servers/
│   └── <server id>/     # one server's /data, bind-mounted into its container
└── backups/
    └── <server id>/     # its backups, <UTC time>.tar.gz
```

The database holds only what Docker doesn't know: accounts, sessions, recovery codes, and which
member may use which server. A server's state, port, type, version and memory live on its container,
and the panel reads them from Docker every time. Back up the whole data folder and you have every
world and every account.

## Mods and plugins

Upload them on a server's Files tab, into `mods/` or `plugins/`. Or put them in
`servers/<server id>/mods/` or `plugins/` yourself, owned by `PUID`:`PGID`. A new server is created
stopped, so they can go in before its first start.

## Backups

A backup is a `.tar.gz` of the server's whole folder, made when someone presses Back up now on its
Backups tab. There is no schedule, and backups are kept until someone deletes them.

- **Making one** works whether the server runs or not. A running server is told to save the world and
  stop writing it (`save-off`) until the backup is done, then to carry on.
- **Restoring** needs the server stopped. The backup is unpacked beside the folder and then swapped
  in, and the folder it replaces is deleted, so back up first if you might want it.
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

Each server's own output is its Console tab, kept by Docker at up to 3 files of 10 MB.
