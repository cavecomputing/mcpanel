# Development

All you need is [uv](https://docs.astral.sh/uv/), and Docker to do anything with servers. Without a
reachable Docker socket the panel still starts and says Docker is unreachable.

```bash
uv sync
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run app.py --debug
MCPANEL_PUBLIC_HOST=localhost MCPANEL_PORT_RANGE=25565-25575 uv run flask --app app create-user matt --admin
uv run pytest
```

The dev server listens on `127.0.0.1:5000` (`--host` and `--port` change that). Data goes in `./data`
unless `MCPANEL_DATA_DIR` says otherwise. The tests fake Docker, so they need no daemon.

See [CLAUDE.md](../CLAUDE.md) for how the code is laid out and the conventions for changing it.
