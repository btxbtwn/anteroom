"""Errors the CLI turns into exit codes."""


class UsageError(Exception):
    """The command line is missing something or names an unknown command."""


class ConfirmRequired(Exception):
    """A destructive call was refused because ``--confirm`` was not passed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ConnectFailed(Exception):
    """The Anteroom server did not accept a connection."""


class ApiError(Exception):
    """The server responded, and the status is not a success."""

    def __init__(self, status: int, message: str, data):
        super().__init__(message)
        self.status = status
        self.message = message
        self.data = data
