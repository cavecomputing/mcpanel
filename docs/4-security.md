# Security

mcpanel is built to be on the internet, behind Caddy. Its sign-in is the whole perimeter, because an
account can start containers on the machine.

## Signing in

- Every page and API route needs a session, except the sign-in pages, `/healthz` and static files.
- Every account has a password (at least 12 characters, stored as an argon2id hash) and TOTP
  two-factor sign-in. A code already used is refused. Each account gets 10 single-use recovery codes.
- Five wrong passwords or codes in a row lock the account for 15 minutes. While it is locked every
  password is wrong, so the lock stops guessing instead of confirming a right guess. An unknown
  username takes as long to refuse as a wrong password, so names can't be probed.
- One-time passwords are 80 random bits, shown once, good for 24 hours, and checked like any password.
- Sessions live in the database, so signing out other devices, removing a user or changing a password
  takes effect at once. The cookie holds only a random token, is `HttpOnly`, `SameSite=Lax` and
  `Secure` behind HTTPS, and lasts 30 days with "Keep this device signed in".
- Changes from another website's page are refused.

## Who may do what

- Admins manage every server and every account. Make admins only people you'd trust with the machine.
- Members see only the servers an admin gave them, and can't create or delete servers or change their
  memory. The server checks this on every request; hiding a button isn't relied on. A member can't
  raise memory through the files either: the image sets Java's memory from the container on every
  start, and the container's memory limit caps the server whatever it asks for.

## Docker

The panel holds the Docker socket, which is root on the machine. So:

- It only ever runs `itzg/minecraft-server`, with one of its Java tags.
- Each server's only mount is its own folder under `servers/`. Never privileged, no host network, no
  extra devices or capabilities, and never any option a client sent.
- Each container's memory is capped at the heap plus headroom, and its log is rotated.
- Console commands go over RCON on the private `mcpanel` network. Nothing in the panel opens a shell
  in a container.

## A server's files

A server's own code is treated as untrusted: a plugin can write anything into its folder.

- The Files tab and backups never follow a link, refuse `.` and `..`, and open only regular files,
  so nothing in the folder leads out of it.
- Downloads are always attachments, so an HTML file in a server's folder can't run as the panel.
- Uploads and saves are capped at 1 GB, the editor at 1 MB.
- The RCON password is kept out of downloads and backups, as
  [Data and backups](3-data-and-backups.md#backups) describes.

## The network

The game ports are open to the internet. Keep the machine on its own VLAN, away from the rest of your
network, and publish the panel's own port only where Caddy can reach it.
