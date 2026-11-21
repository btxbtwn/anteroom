"""Serve the hosted API on Fly: ``python -m anteroom.hosted_server``.

One socket takes both IPv4, from Fly's proxy, and IPv6, from another Machine
on Fly's private network forwarding a Mac or Chrome command. Uvicorn's own
``--host ::`` would refuse IPv4. A stop signal starts handing scheduled work
and devices to the next Machine before open requests finish draining.
"""
from __future__ import annotations

import socket
import sys
from types import FrameType

import uvicorn

from anteroom.services import process_handoff


def dual_stack_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(('::', port))
    sock.set_inheritable(True)
    return sock


class HostedServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.worker_failed = False

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        process_handoff.begin_draining()
        super().handle_exit(sig, frame)

    def exit_on_worker_failure(self, _exc: BaseException | None = None) -> None:
        # Fly checks route and gate deploys, but do not restart a running Machine
        # until its process exits. Drain leases and exit non-zero so Fly restarts it.
        self.worker_failed = True
        process_handoff.begin_draining()
        self.should_exit = True


def main() -> None:
    from anteroom import hosted

    config = uvicorn.Config(
        'anteroom.hosted:app', access_log=False, timeout_graceful_shutdown=120,
    )
    server = HostedServer(config)
    hosted.set_worker_stopped_handler(server.exit_on_worker_failure)
    try:
        server.run(sockets=[dual_stack_socket(8080)])
    finally:
        hosted.set_worker_stopped_handler(None)
    if server.worker_failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
