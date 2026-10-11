# Getting started

This page is about using the panel once it is running. To install it, see
[Setup](2-setup.md).

## Signing in for the first time

Every account starts with a one-time password, made by an admin. It works once, for 24 hours.
(The platform admin is made in the browser on the panel's first visit, and starts at step 2: see
[Setup](2-setup.md#the-platform-admin).)

1. Sign in with your username and the one-time password.
2. Choose your own password, at least 8 characters.
3. Scan the QR code with an authenticator app (any TOTP app) and type in the code it shows.
4. Keep the recovery codes the panel shows next. You see them once. Each one signs you in once in
   place of a code, for when you don't have your phone.

From then on, signing in is your password and then a 6-digit code from the app, or a recovery code.
(If the panel's owner switched two-factor sign-in off, steps 3 and 4 are skipped and the password is
enough. If they switch it on later, you are asked to do step 3 after your password next time.)
Tick "Keep this device signed in" to stay signed in for 30 days.

If you lose both your phone and your recovery codes, an admin can reset your sign-in from the Users
page: you get a new one-time password and set up two-factor sign-in again.

## Platform admins, admins and members

There are three kinds of account:

- **Platform admins** do everything admins do, and are the only ones who add, change, reset or remove
  admins and platform admins. The first account on a panel is its platform admin. A platform admin
  can make someone else one too, and the panel always keeps at least one.
- **Admins** see and manage every server, and add and manage members. Only admins and platform
  admins create and delete servers, and only they change how much memory and CPU a server gets.
- **Members** see only the servers an admin gave them. On those, they do everything else:
  start and stop it, its console, players, files, mods, backups, game settings, type, version and
  Java. They can't delete a server.

A panel from before there were platform admins makes its earliest admin the platform admin the
first time it starts after the update.

### Adding someone

On the Users page, an admin adds an account and ticks the servers it may use. The panel shows that
account's one-time password once: pass it on, and they follow the steps above. From the same page an
admin changes a member's servers, resets their sign-in, or removes them. Admins only add and manage
members; a platform admin also picks the role (platform admin, admin or member) and manages admins.

### Your account

The account menu in the corner opens Your account, where you change your password, make a new set of
recovery codes (the old ones stop working), and see the devices you are signed in on and sign any of
them out.

## Creating a server

Admins press New server and choose:

- **Name**: what the panel calls it
- **Type**: Vanilla, Paper, Purpur, Fabric, Forge, NeoForge or Quilt
- **Version**: `LATEST`, or a Minecraft release such as `1.21.4`
- **Memory**: the server's Java heap, 1 to 32 GB. Its container's memory limit is the heap plus room
  for Java itself: a quarter on top, at least 1 GB. So a 1 GB server may take up to 2 GB of the
  machine's memory, and an 8 GB one 10 GB.
- **CPU**: how many of the machine's cores it may use, in steps of half a core, for example `2` or
  `1.5`. Leave it empty and it may use them all.
- **Java**: leave it on "Match the version" and the panel picks the Java that version needs, or pick
  one yourself
- **The EULA box**: Minecraft's EULA has to be accepted before a server will run

Under the memory and CPU fields, the dialog shows the machine's memory and cores, and how much of
them the existing servers are given (each one's memory limit, running or not, and its CPU limit), so
you can see what is left. The Settings tab shows the same for the other servers when an admin changes
one.

A new server is created stopped and stays stopped until someone presses Start, so mods, plugins and
other files can go in its folder before the world is made. Each server gets the lowest free port in
the panel's range; its address, shown in its header, is what players type.

The first start downloads the server and, for modded types, installs the loader, so it can take a few
minutes. With `LATEST`, Minecraft updates itself on every start.

## A server's tabs

Start, Stop and Restart sit in the server's header. Below it:

- **Overview**: its state, address, type, version, heap and memory limit, and while it runs the
  players online, CPU, and the memory in use out of that limit (Java's own memory included, not
  just the heap).
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
  simulation distance, flight, whitelist, online mode, seed), saved into `server.properties` and
  used from the next start. PvP is different from Minecraft 1.21.9 on, where it is a game rule kept
  in the world: it changes right away, only while the server is running, and is greyed out while
  it's stopped. A server moved from an older version to 1.21.9 or later starts with PvP on whatever
  the file said, so check it after the first start. Below them, the server's own settings: its name,
  type, version and Java, and for admins its memory and CPU. Those change only while it is stopped, because
  the panel makes its container again with them (its world, files and port stay). Admins and platform
  admins also delete the server here, once it is stopped, which removes its folder and its backups.
