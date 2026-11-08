"""A keyring that keeps secrets in memory, for sandbox mode and tests."""

from __future__ import annotations

from jaraco.classes import properties
from keyring.backend import KeyringBackend


class MemoryKeyring(KeyringBackend):
    """A keyring that forgets everything when the process ends."""

    @properties.classproperty
    def priority(cls) -> float:
        return 1.0

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)
