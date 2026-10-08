# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

mcpanel (working name) is a small web panel for hosting Minecraft servers on one machine. It runs in
its own Docker container, talks to the host's Docker socket the way Portainer does, and runs every
Minecraft server as its own [`itzg/minecraft-server`](https://github.com/itzg/docker-minecraft-server)
container. A handful of people sign in to it over the internet, behind a Caddy reverse proxy that
terminates TLS.

The app is early. Sections marked _(open)_ record decisions still being made; update them as they
settle rather than working around them. If the project is renamed, the package, the `MCPANEL_`
environment variables, the cookie and the Docker labels all change together.

## What it does, and what it deliberately doesn't

- **One container per server, always `itzg/minecraft-server`.** The panel never runs Java itself,
  never runs a second kind of image, and never runs a separate agent or daemon. Servers keep running
  when the panel restarts or crashes.
- **Docker is the source of truth for servers.** A server is a container carrying the
  `mcpanel.server` label; the panel finds its servers by that label after a restart and keeps no
  copy of what Docker already knows (state, port, image, settings in the container's env).
- **One port per server: the Minecraft game port.** It comes from `MCPANEL_PORT_RANGE` and is
  published on the host; players connect to `MCPANEL_PUBLIC_HOST:<port>`. RCON stays on the private
  `mcpanel` Docker network and is never published. No second ports (voice chat, Geyser/Bedrock,
  query): not now, not as an option.
- **Users:** a few accounts, each `admin` or `member`. Admins manage everything, including users;
  members see and run only the servers an admin gave them. New accounts come from one-time invite
  links. Every account has a password and TOTP two-factor sign-in, with recovery codes.
- **It is on the public internet.** Treat every route as reachable by strangers. See Access below.
- **No logging knobs.** Logging uses fixed defaults (see Logging). Don't add `LOG_*` variables.

## Git workflow

Commit as the work goes and **push straight to `main` without asking**: this project uses no
feature branches or PRs, and the user does not review before a push. Only include files belonging
to the task, and leave unrelated pre-existing changes and stray untracked files alone. Push once a
piece of work is done and verified, not half-finished. This applies to this repository only; never
push to any other.

**One commit, one concern.** A commit has to be reviewable on its own and safe to revert on its own,
so split unrelated work rather than bundling it: a bug fix and a docs correction that happened to
land in the same session are two commits. Size is not the test — a change that genuinely touches
twenty files is still one commit if it is one concern. Say in the message what broke and why the fix
works, not just what you typed.

This file is the only agent doc. Don't add an `AGENTS.md` or a second copy of these rules.

## Least code that does the job

Aim for the smallest diff that completes the task in full. This is a vanilla-JS, no-build,
single-process app, and it stays approachable only while changes stay small.

- Prefer editing existing code over adding new code, and a few lines at the right call site over a
  new helper, module or class. Add a file only when something forces it.
- Reuse what is already here: `db.get_db()`, `api/common.py`, `servers.py`, `api.js`, `ui.js`, the
  design system's `cc-*` components. A second implementation of something the repo already does is
  the most expensive kind of code.
- **No new dependency without asking first.**
- Leave out what nothing needs yet: config knobs, feature flags, an abstraction with one caller,
  `try`/`except` around code that doesn't raise, re-validation of data the route already validated.
- Deleting code is a legitimate way to finish a task. Say so when the fix turns out to be a removal.

This governs the amount of code, not the amount of work. Deliver everything that was asked and run
the verification the change calls for.

## Naming

A name is the cheapest documentation in the file. Make it a concise nameplate for what the thing is
or does — long enough to be unambiguous where it is *used*, short enough to read at a glance.

- Prefer the specific noun to the category: `game_port`, `heap_gb`, `server_ids` over `value`,
  `data`, `items`.
- Name a function for what it returns or does, not how it works — `free_port()`,
  `container_spec()`, `formatUptime()`.
- Don't encode the type or the scope in the name (`serverObjDict`, `tmpList`), and don't abbreviate
  past recognition.
- Follow the module you are editing — `snake_case` in Python, `camelCase` in JS — over any
  preference of your own.

Renaming existing code is its own task. Don't fold a rename into an unrelated change, where it
buries the real diff under noise.

## Run / test

Flask backend, vanilla-JS frontend with no build step, same shape as cozy, imgy and binny: SQLite,
`uv`, pytest, Docker.

```bash
uv sync                                        # install the locked dependencies into .venv

# Dev server on 127.0.0.1:5000 (--host / --port to change). Needs a reachable Docker socket to do
# anything with servers; without one the panel still starts and says Docker is unreachable.
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run app.py --debug
MCPANEL_DATA_DIR=/path/to/data ...             # data directory (default: ./data)

# The first account: prints a one-time invite link for an admin.
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run flask --app app invite matt --admin

uv run pytest                                  # full suite (use `uv run`, not bare pytest)
uv run pytest tests/test_auth.py::test_sign_out -x
node --check mcpanel/static/js/<file>.js       # frontend syntax check; there is no JS test suite

docker compose -f docker/compose.yml up --build   # settings go in docker/.env
```

The app refuses to start without `MCPANEL_PUBLIC_HOST` and a valid `MCPANEL_PORT_RANGE`.

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`; Python is pinned in
`.python-version`. Change them with `uv add` / `uv remove` (or edit `pyproject.toml` and run
`uv lock`) and commit both files together. Never `pip install` into the project, and never bump the
`0.0.0` in `pyproject.toml` — it is a packaging placeholder, not a version.

### How much verification a change needs

Run the tests that cover what was touched, and the full suite before every push. `uv run pytest` is
fast, so when in doubt run all of it.

**Booting the dev server is not required after every change.** Start it when the change can only be
confirmed in a browser — layout, theming, mobile behaviour, the sign-in flow — or when asked. For
backend logic, docs and comments, skip it and say so. Never claim a change was verified in the app,
or against a real Docker daemon, when it wasn't.

## Architecture

Single-process Flask app, vanilla-JS frontend, no build step. The layout is meant to be readable at
a glance: one thing per file, named for what it owns, so finding the code for a feature never takes
a search. Keep it that way — a new feature gets its own blueprint or module rather than growing a
neighbour.

| Path | Owns |
|---|---|
| `app.py` | Entry point, and the **only** Python file at the repo root: `app = create_app()`, which `uv run app.py`, `flask --app app` and `gunicorn app:app` all name. No logic lives here. |
| `mcpanel/` | The application package. Everything else in Python goes in here. |
| `mcpanel/__init__.py` | `create_app()`: logging and the request log, `ProxyFix`, cookie settings, `init_db()`, the cross-site write guard (`reject_cross_site_writes()`), the login guard, blueprints, the `invite` CLI command. |
| `mcpanel/config.py` | Settings read once from the environment: paths under `MCPANEL_DATA_DIR`, `MCPANEL_PUBLIC_HOST`, `MCPANEL_PORT_RANGE`. |
| `mcpanel/db.py` | Schema (`init_db()`, idempotent) and `get_db()`, a short-lived connection per use. |
| `mcpanel/logs.py` | `setup_logging()`: JSON lines on stdout, the same lines in a rotated file, the audit log, and the filter that strips secrets. `audit()` records who did what; `start_timer()` and `log_request()` write one line per request. |
| `mcpanel/auth.py` | The sign-in pages: `/login` (password), `/login/code` (TOTP or a recovery code), `/logout`, `/invite/<token>`. The session cookie and `require_login()` in front of everything else, `require_admin()`, lockouts and the sign-in throttle (`throttled()`), the `invite` command. |
| `mcpanel/accounts.py` | Users, sessions, invites and recovery codes in the database: password hashing (argon2id), TOTP secrets and checks, who may see which server. |
| `mcpanel/servers.py` | Everything that talks to Docker: finding the panel's containers by label, `container_spec()` (the one place a container's settings are decided), `free_port()`, create, start, stop, restart, and `docker_errors()`, which turns Docker's errors into the messages the UI shows. |
| `mcpanel/views.py` | `index()` serves the page; `healthz()` answers `/healthz`, open to anyone (`auth.OPEN_ENDPOINTS` names it). |
| `mcpanel/api/` | One Flask blueprint per resource, all under `/api`: `servers` (list, options, create, start, stop, restart), `users` (admin: list, invite, change, remove, revoke an invite), `account` (me, my sessions, password, recovery codes). `__init__.py` mounts them and answers every error as `{"error": message}`. `common.py` has `json_body()`, the request's JSON object or a 400; the fields are checked where they are used (`servers.create_server()`, `users.py`). |
| `mcpanel/templates/` | `base.html` (head, the theme script, the cube icon), `login.html` (the password and code steps), `invite.html` (setting up an account, then its recovery codes once), `index.html` (the app shell, its dialogs and the icon sprite). |
| `mcpanel/static/js/` | ES modules, one per concern, entry `main.js` loaded with `<script type="module">`: `api.js`, `ui.js` (escaping, the toast, the question dialog), `state.js`, `theme.js`, `servers.js` (sidebar list polled every 5 s, the phone drawer, server header and actions, new-server dialog), `users.js` (the Users page and its invite/change dialog), `account.js` (the account menu, and the Your account page: password, recovery codes, signed-in devices). |
| `mcpanel/static/css/` | `cavecomputing.css` (the design system's `bundle.css`, copied unchanged) and `style.css` (the tokens and the panel's own layout). |
| `mcpanel/static/favicon.svg` | The logo (see Frontend conventions), also the README's picture. |
| `tests/` | pytest, one file per blueprint or module, and `fake_docker.py` (`FakeDocker`). Docker is faked; nothing in the suite needs a daemon. |
| `docker/` | `Dockerfile`, `Dockerfile.dockerignore`, `compose.yml` and `entrypoint.sh`, as in binny: gunicorn with one gthread worker on port 5000, the data folder handed to `PUID`:`PGID` with `setpriv` (worlds under `servers/` left alone), plus the Docker socket's group. |

Fill in the "Owns" column with real names as modules land, and add the rules the code can't tell you
on its own under it:

- **Run one worker process** (threads are fine). Port allocation and the sign-in throttle hold
  locks in memory.

### Configuration and data

```
data/                    # MCPANEL_DATA_DIR, default ./data
├── mcpanel.db           # SQLite: users, sessions, invites, recovery codes, server access
├── logs/
│   ├── mcpanel.log      # everything on stdout, rotated
│   └── audit.log        # who did what, rotated
└── servers/
    └── <server id>/     # one server's /data, bind-mounted into its container
```

- `MCPANEL_PUBLIC_HOST` is the name players type (shown as the server's address); `MCPANEL_PORT_RANGE`
  (`25565-25600`) is the host ports the panel may publish, one per server. The router forwards the
  same range. That is the whole configuration; don't add knobs.
- **`MCPANEL_DATA_DIR` must be the same path inside the panel's container as on the host.** A
  server's data is a bind mount, and Docker reads bind-mount paths on the host, so the panel passes
  `<data dir>/servers/<id>` as the host path and also reads and writes it itself. `compose.yml`
  mounts `${MCPANEL_DATA_DIR}:${MCPANEL_DATA_DIR}` for that reason. Never translate paths.
- **The database holds only what Docker doesn't know:** accounts, sessions, invites, recovery codes
  and which member may see which server. A server's state, port and settings are read from Docker
  every time. A server id that Docker no longer has is stale, not an error. Nothing in it may sign
  anyone in: the key for Flask's cookie (a sign-in halfway through) is random for each process,
  since kept there, a copy of the database could sign that cookie and skip the password.
- Every module reads settings as `config.NAME` at call time, never `from .config import NAME`, so
  the tests can point them at a temporary directory.

### Docker rules

The panel holds the Docker socket, which is root on the host. Whoever can make the panel run an
arbitrary container owns the machine, so:

- **`servers.container_spec()` is the only place a container's settings are decided**, from checked
  fields. The image is always `itzg/minecraft-server` with a tag from `JAVA_TAGS`; the only mount is
  `<data dir>/servers/<id>:/data`; never `privileged`, never extra mounts, devices, capabilities,
  host networking or the host's PID namespace. Never pass a client's options, env or labels through.
- Server ids are lowercase `[a-z0-9-]`, checked before they reach a name, label or path
  (`servers.check_id()`).
- Each container gets: labels `mcpanel.server=<id>` and `mcpanel.name=<display name>`; name
  `mcpanel-<id>`; the `mcpanel` network; restart `unless-stopped`; `EULA=TRUE` only after the user
  ticked the EULA box; `TYPE`, `VERSION` and `MEMORY` from the form; `UID`/`GID` matching the panel's,
  so the panel can read and edit the server's files; a random `RCON_PASSWORD`; a memory limit of the
  heap plus headroom; json-file logging capped at 3 × 10 MB, because Docker's default never rotates;
  a stop timeout of `STOP_SECONDS` (60 s), so a reboot or an outside `docker stop` gives the world as
  long to save as the panel's Stop does, not Docker's default 10 s.
- `free_port()` takes the lowest port in the range that no container (running or stopped) has
  published, under a lock. Docker refuses a port something outside Docker holds; report that, don't
  retry blindly.
- RCON goes over the `mcpanel` network to `mcpanel-<id>:25575`. Commands from the console go
  through RCON, never `docker exec`; nothing in the panel opens a shell in a container.

### Access

The panel is on the public internet behind Caddy, and an account can start containers, so sign-in is
the whole perimeter:

- **Every route except sign-in, the invite page, `/healthz` and static assets requires a session.**
  A new route is behind the check by default, not opted in. Admin-only API routes use
  `require_admin()`; anything a member does to a server checks `accounts.may_use(user, server_id)`.
- Passwords are argon2id hashes (at least 12 characters). TOTP is required on every account: a
  sign-in is password, then a 6-digit code (a code already used is refused) or a single-use recovery
  code. 5 wrong passwords or codes in a row lock that account for 15 minutes, and while it is locked
  every password is wrong (the dummy hash is checked), so the lock stops guessing instead of
  confirming the right one. An unknown username costs the same time as a wrong password, so names
  can't be probed.
- Sessions live in the database, so signing out other devices, removing a user and changing a
  password take effect at once. The cookie (`mcpanel_session`) holds only a random token, stored
  hashed; it is `HttpOnly`, `SameSite=Lax`, `Secure` behind HTTPS (`X-Forwarded-Proto` via
  `ProxyFix`), and lasts 30 days when "Keep this device signed in" is ticked.
- Invites are single-use, expire after 24 hours and are stored hashed. The link is shown once.
- Writes from another site's page are refused (`reject_cross_site_writes()`, the same check as
  binny, imgy and cozy), so keep state-changing routes on POST/PUT/DELETE.
- Never put a password, TOTP secret or code, recovery code, session or invite token, or RCON password
  in a log line, an error message, the audit log or a response other than the one that creates it.

### Logging

Fixed defaults, no settings:

- One JSON object per line on stdout at `info` (`ts`, `level`, `logger`, `msg`, plus fields passed
  with `extra=`); plain readable lines instead when stdout is a terminal. The same lines go to
  `data/logs/mcpanel.log`, rotated at 10 MB, 7 files kept, gzipped.
- `logs.audit(event, **fields)` records sign-ins, failed tries, lockouts, account changes, and every
  server action to `data/logs/audit.log` (same rotation) as well as stdout, with the acting user and
  the client IP.
- The secrets filter in `logs.py` masks any field whose name says it is a secret. It is a backstop,
  not permission to pass secrets to a logger.

### Frontend conventions

- Native ES modules only, `import`/`export` with relative paths. No bundler, no framework, no
  globals except what `main.js` deliberately wires up.
- Call the server through `api.js`, which throws on errors and shows them to the user. Modules that
  bind listeners export an `initX()`; `main.js` calls them in order.
- The look is the **cavecomputing design system**
  ([reference](https://claude.ai/artifact/TAYcpHgxU55sLKKU2sYeRv): read its `project/README.md`,
  `project/tokens.json` and `project/components/bundle.css`, and its SignIn component for the sign-in
  card). Copy its `bundle.css` unchanged into `mcpanel/static/css/cavecomputing.css` and build on the
  `cc-*` components before writing new ones. Change the design system, not the copy.
- Colors come from its tokens; never hardcode them. Accents keep one meaning: yellow (`accent-text`)
  for focus, selection and active rows; green (`accent-alt`) for section icons; aqua for running and
  done; blue (`accent-cool`) for addresses, paths and links; orange (`accent-warm`) for anything
  destructive and for crashed. There is no red. No web fonts: system mono and sans only.
- **Dark is the default theme**, whatever the OS prefers. The choice lives in `localStorage`
  (`mcpanel-theme`), applied by a tiny inline script in `<head>` before the stylesheet paints.
- **Logo and favicon match the sibling apps** (cozy yellow, imgy green, campfire orange, binny aqua):
  a `#282828` rounded square with a 24px stroked line icon, 2.2 stroke, round caps. The panel's is a
  block (an isometric cube) in rose, `#d3869b` (`--logo`; `#8f3f71` in light).
- The [interface mockup](https://claude.ai/artifact/4HS87KMKREcD4qCZ7nkeeA) is the reference for
  layout: server list in the sidebar, server header with Start/Stop/Restart, the Overview, Console,
  Players, Files, Backups and Settings tabs, the Users page, the sign-in and TOTP cards. Only the
  parts that are built appear in the app; no placeholder tabs.

## Testing gotchas

[tests/conftest.py](tests/conftest.py) sets the required `MCPANEL_*` variables before `app.py` or
`mcpanel` is imported, and its `data_dir` fixture points `mcpanel.config`'s paths at a temporary
directory with `monkeypatch`. That only works because every module reads them as `config.DATA_DIR`
at call time; a `from .config import DATA_DIR` binds the value at import, the patch never reaches
it, and the tests quietly start writing into the real `data/`.

Docker is replaced by `FakeDocker` from `tests/fake_docker.py` (conftest's `docker` fixture patches
`servers.docker_client`), so tests never need a daemon. Use the `admin` and `member` fixtures for
signed-in clients, `anon` for one that isn't, and conftest's `sign_in()` helper to drive the
password-then-TOTP flow.
