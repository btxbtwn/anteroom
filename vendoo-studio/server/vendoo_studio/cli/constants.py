"""Shared CLI defaults.

Anteroom listens on loopback port 4318 unless ``VENDOO_STUDIO_PORT`` says
otherwise. The CLI does not import the server to learn that, so starting a
command does not create the app's data folder.
"""

CLI_NAME = "anteroom"
DEFAULT_BASE_URL = "http://127.0.0.1:4318"
DEFAULT_TIMEOUT = 120.0
DEFAULT_STREAM_SECONDS = 20.0

# Groups the CLI reserves for its own commands. A generated route group that
# collides with one of these is prefixed so the route commands stay reachable.
RESERVED_GROUPS = frozenset({"auth", "api", "commands", "mcp", "version", "help"})

AUTH_NOTE = (
    "Anteroom's HTTP API does not check a bearer token. It is built to listen "
    "on 127.0.0.1. The CLI can store a token per environment and send it as "
    "Authorization: Bearer when a proxy in front of another URL requires one. "
    "Tailscale Serve, turned on in the app, is what protects a remote URL."
)

EXIT_OK = 0
EXIT_API = 1
EXIT_USAGE = 2
EXIT_CONNECT = 3
EXIT_CONFIRM = 4
