"""Serve the hosted API on Fly: ``python -m vendoo_studio.hosted_server``.

One socket takes both IPv4, from Fly's proxy, and IPv6, from another Machine
on Fly's private network forwarding a Mac or Chrome command. Uvicorn's own
``--host ::`` would refuse IPv4. A stop signal starts handing scheduled work
and devices to the next Machine before open requests finish draining.
"""
from __future__ import annotations

import socket
from types import FrameType

import uvicorn

from vendoo_studio.services import process_handoff


def dual_stack_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(('::', port))
    sock.set_inheritable(True)
    return sock


class HostedServer(uvicorn.Server):
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        process_handoff.begin_draining()
        super().handle_exit(sig, frame)


def main() -> None:
    config = uvicorn.Config(
        'vendoo_studio.hosted:app', access_log=False, timeout_graceful_shutdown=120,
    )
    HostedServer(config).run(sockets=[dual_stack_socket(8080)])


if __name__ == '__main__':
    main()
