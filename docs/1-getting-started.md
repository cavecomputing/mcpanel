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
  admins create and delete servers, and only they change a server's name, type, version, Java,
  memory and CPU.
- **Members** see only the servers an admin gave them. On those, they start and stop it and use its
  console, players, files, mods, backups and game settings. They see the server's own settings but
  can't change them, and can't delete a server.

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
- **Memory**: all the server may use, 1 to 32 GB. Three quarters of it is Java's heap (`-Xms` and
  `-Xmx`); the rest is for Java itself (loaded classes, compiled code, threads, network buffers),
  which a heap as big as the limit would leave no room for, and the server would be killed. So a
  1 GB server has a 768 MB heap.
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
minutes; while it says Starting, the header shows the last line it wrote, such as "Preparing spawn
area: 45%". With `LATEST`, Minecraft updates itself on every start.

## A server's tabs

Start, Stop and Restart sit in the server's header; Stop and Restart ask first while players are on,
since they disconnect them. The question offers to warn them in game first, ticked to start with:
the server says it stops (or restarts) in 30 seconds, then in 10 seconds, and goes ahead 30 seconds
after you pressed it. Below it:

- **Overview**: in Health, how many players are on out of how many it takes, its TPS (ticks a
  second, 20 when it keeps up and fewer when it lags), memory, CPU and disk,
  each with a bar against its limit that turns orange near it (TPS under 18, memory over 90%); in
  Coming up, how long it has been up, its next scheduled restart and backup, and its newest backup.
  TPS comes from the server's own command (`tps` on Paper and Purpur, `forge tps`, `neoforge tps`,
  and `tick query` on the rest), which vanilla, Fabric and Quilt have only from 1.20.3, so before
  that it shows a dash. Disk is what the server takes on the disk, its folder (world, mods,
  settings, logs) and its backups together, running or not, with the two apart and what the disk
  has free below; it is measured when the Overview opens and then once a minute, as Coming up is.
  When it has crashed, isn't answering or keeps crashing, the last lines it wrote come first, and
  in plain words what they most likely mean when it's a common cause: out of memory, a Java too old
  for it, or a mod missing another mod or made for another version.
- **Console**: the server's output as it runs, and a box for commands, which go to the server over
  RCON, with or without the leading `/`. Buttons under the output run common ones: `list`,
  `save-all`, `say` and `difficulty` (which fill the box for you to finish), day and night, clear
  weather, `seed`, TPS (with the server type's own command), `plugins` on Paper and Purpur, a
  cookie for everyone online, and clearing the items dropped on the ground, which asks first. The
  latest.log button downloads `logs/latest.log`, the server's log since it last started, which is
  what people ask for when they help with a problem.
- **Players**: All lists everyone who has ever joined, with when they were last on (from the
  world's player files, so to within a few minutes); then who is online, and the whitelist,
  operators and bans, each a click to change. Each player is tagged with what else they are (online,
  operator, whitelisted, banned). Kick asks first, and Ban asks for a reason, which the player sees
  when they try to join; Banned shows each ban's reason, when it was made and who made it (the panel
  user, or whoever the server says for a ban made another way), and can offer to take them off the
  whitelist too. While it runs, changes are console commands. While it's stopped, the panel
  writes them into the server's own list files, so the whitelist can be set before the first start;
  it looks each player's account up with Mojang, unless online mode is off.
- **Files**: the server's folder. Browse it, open and edit text files up to 1 MB (such as
  `server.properties`), upload files up to 1 GB (mods, plugins, a world) with the button or by
  dropping them on the list, extract a `.zip` where it is, download, make folders, rename and
  delete. The editor asks before it drops changes you haven't saved.
- **Backups**: back up the whole folder, download a backup, restore one, or delete it. See
  [Data and backups](3-data-and-backups.md#backups).
- **Schedule**: restarts and backups at set times. See [Schedules](#schedules).
- **Settings**: the game settings (MOTD, max players, difficulty, game mode, hardcore, PvP, view and
  simulation distance, flight, whitelist, online mode, seed), saved into `server.properties` and
  used from the next start (saved while it runs, a Restart button appears beside Save). PvP is
  different from Minecraft 1.21.9 on, where it is a game rule kept in the world: it changes right
  away, only while the server is running, and is greyed out while it's stopped. A server moved from
  an older version to 1.21.9 or later starts with PvP on whatever the file said, so check it after
  the first start. Below them, the server's own settings: its name, type, version, Java, memory and
  CPU, which only admins change (members see them greyed out). Those change only while it is
  stopped, because the panel makes its container again with them (its world, files and port stay).
  Admins and platform admins also delete the server here, once it is stopped, which removes its
  folder and its backups.

## Schedules

The Schedule tab restarts a server, or backs it up, at times written as a cron line: minute, hour,
day of the month, month and day of the week. Anyone who may use the server can add or remove them,
and a server can have as many of each as you like.

| Line | Runs |
|---|---|
| `0 4 * * *` | every day at 4:00 |
| `0 */6 * * *` | every 6 hours, on the hour |
| `30 5 * * mon` | 5:30 on Mondays |
| `0 4 * * 1-5` | 4:00 on weekdays |
| `@daily` | midnight |

Each field is `*`, a number, a range (`1-5`), a list (`1,15`), or a step after `*` or a range
(`*/15`, `0-30/10`). Months and days of the week can be written as `jan`-`dec` and `sun`-`sat`;
Sunday is 0 or 7. As in Linux's cron, when both day fields are set, either one will do:
`0 4 1 * mon` is the 1st of the month and every Monday. `@hourly`, `@daily`, `@weekly`, `@monthly`
and `@yearly` work too.

A line is read in the time zone of the browser that added it. The Schedule tab lists each kind in a
table, with when each line next happens, and adds a line from the box under the table. When the clocks change for daylight saving, a time in the skipped hour comes just
after it, and one in the repeated hour comes once, as with cron. Anything due while the panel itself
is down is skipped, not made up later.

- **Restarts**: players are told in game 5 minutes, 1 minute and 10 seconds before. A server that
  isn't running by then (stopped, crashed or still starting) is left as it is.
- **Backups** are made as Back up now makes them, whether the server runs or not, and appear on the
  Backups tab marked with the line's Name (optional, such as "Nightly"; the pencil beside the line
  changes it) or its cron line. Each line keeps its newest backups, as many
  as its Keep says, and deletes its own older ones after each new one. It never deletes a backup
  made with Back up now or by another line, and removing a line keeps the backups it made. One that
  comes while another backup or a restore of the server is still under way is skipped. See
  [Data and backups](3-data-and-backups.md#backups).
