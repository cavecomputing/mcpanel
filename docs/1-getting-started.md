# Getting started

This page is about using the panel once it is running. To install it, see
[Setup](2-setup.md).

## Signing in for the first time

Every account starts with a one-time password, made by an admin (or, for the first admin, by the
`create-user` command in [Setup](2-setup.md#the-first-admin)). It works once, for 24 hours.

1. Sign in with your username and the one-time password.
2. Choose your own password, at least 12 characters.
3. Scan the QR code with an authenticator app (any TOTP app) and type in the code it shows.
4. Keep the recovery codes the panel shows next. You see them once. Each one signs you in once in
   place of a code, for when you don't have your phone.

From then on, signing in is your password and then a 6-digit code from the app, or a recovery code.
Tick "Keep this device signed in" to stay signed in for 30 days.

If you lose both your phone and your recovery codes, an admin can reset your sign-in from the Users
page: you get a new one-time password and set up two-factor sign-in again.

## Admins and members

There are two kinds of account:

- **Admins** see and manage every server and every account. Only admins create and delete servers,
  and only admins change how much memory a server gets.
- **Members** see only the servers an admin gave them. On those, they do everything else: start and
  stop it, its console, players, files, mods, backups, game settings, type, version and Java.

### Adding someone

On the Users page, an admin adds an account, picks admin or member, and for a member ticks the servers
they may use. The panel shows that account's one-time password once: pass it on, and they follow the
steps above. From the same page an admin changes someone's role or servers, resets their sign-in, or
removes them.

### Your account

The account menu in the corner opens Your account, where you change your password, make a new set of
recovery codes (the old ones stop working), and see the devices you are signed in on and sign any of
them out.

## Creating a server

Admins press New server and choose:

- **Name**: what the panel calls it
- **Type**: Vanilla, Paper, Purpur, Fabric, Forge, NeoForge or Quilt
- **Version**: `LATEST`, or a Minecraft release such as `1.21.4`
- **Memory**: the server's heap, 1 to 32 GB. Its container may use a little more than that for Java
  itself (a quarter on top, at least 1 GB).
- **Java**: leave it on "Match the version" and the panel picks the Java that version needs, or pick
  one yourself
- **The EULA box**: Minecraft's EULA has to be accepted before a server will run

A new server is created stopped and stays stopped until someone presses Start, so mods, plugins and
other files can go in its folder before the world is made. Each server gets the lowest free port in
the panel's range; its address, shown in its header, is what players type.

The first start downloads the server and, for modded types, installs the loader, so it can take a few
minutes. With `LATEST`, Minecraft updates itself on every start.

## A server's tabs

Start, Stop and Restart sit in the server's header. Below it:

- **Overview**: its state, address, type and version, and while it runs the players online, memory
  and CPU.
- **Console**: the server's output as it runs, and a box for commands, which go to the server over
  RCON, with or without the leading `/`.
- **Players**: who is online, and the whitelist, operators and bans, each a click to change. Changes
  are console commands, so the server has to be running.
- **Files**: the server's folder. Browse it, open and edit text files up to 1 MB (such as
  `server.properties`), upload files up to 1 GB (mods, plugins, a world), download, make folders,
  rename and delete.
- **Backups**: back up the whole folder, download a backup, restore one, or delete it. See
  [Data and backups](3-data-and-backups.md#backups).
- **Settings**: the game settings (MOTD, max players, difficulty, game mode, hardcore, PvP, view and
  simulation distance, flight, whitelist, online mode, seed), saved into `server.properties` and used
  from the next start. Below them, the server's own settings: its name, type, version and Java, and
  for admins its memory. Those change only while it is stopped, because the panel makes its container
  again with them (its world, files and port stay). Admins also delete the server here, once it is
  stopped, which removes its folder and its backups.
