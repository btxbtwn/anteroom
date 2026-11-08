"""Seller-scoped routing of Mac and Chrome commands to the process the device polls.

Command payloads stay in that process. Another process forwards over the
private address recorded for the seller. A missing route, a stale route, or a
failed forward fails the call; it is not tried again on a different machine.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

log = logging.getLogger(__name__)
_route_warning = False

STALE_SECONDS = {'mac': 45, 'chrome': 30}
_MACHINE = re.compile(r'^[A-Za-z0-9]{1,64}$')
_URL = re.compile(r'^http://(\[[0-9a-f:]+\]|127\.0\.0\.1):[0-9]{2,5}$')


class RoutingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceRoute:
    machine_id: str
    base_url: str
    owned: bool


def _warn_route(message: str, exc: BaseException) -> None:
    global _route_warning
    if _route_warning:
        return
    _route_warning = True
    log.warning('%s (%s)', message, type(exc).__name__)


def machine_id() -> str:
    return os.environ.get('FLY_MACHINE_ID', '')


def routing_enabled() -> bool:
    return bool(machine_id())


def local_base_url() -> str:
    override = os.environ.get('ANTEROOM_PROCESS_BASE_URL', '')
    if override:
        return override
    address = os.environ.get('FLY_PRIVATE_IP', '')
    if not address:
        return ''
    host = f'[{address}]' if ':' in address else address
    return f'http://{host}:8080'


def _engine(engine):
    if engine is not None:
        return engine
    from vendoo_studio.database import engine as default
    return default


def _owner(owner: str) -> str | None:
    try:
        return str(UUID(owner))
    except (TypeError, ValueError):
        return None


def claim(owner: str, device: str, engine=None) -> DeviceRoute | None:
    """Record that this process holds the seller's device, or None when routing is idle."""
    if not routing_enabled() or device not in STALE_SECONDS:
        return None
    identity = _owner(owner)
    url = local_base_url()
    current = machine_id()
    if identity is None or not _URL.match(url) or not _MACHINE.match(current):
        return None
    try:
        with _engine(engine).begin() as connection:
            row = connection.execute(text(
                'SELECT machine_id, base_url, owned FROM anteroom_private.note_device_route'
                '(:owner, :device, :machine, :url, :stale)'
            ), {'owner': identity, 'device': device, 'machine': current, 'url': url,
                'stale': STALE_SECONDS[device]}).first()
    except SQLAlchemyError as exc:
        _warn_route('Device route was not recorded', exc)
        return None
    if row is None:
        return None
    return DeviceRoute(row.machine_id, row.base_url, bool(row.owned))


def lookup(owner: str, device: str, engine=None) -> DeviceRoute | None:
    if not routing_enabled() or device not in STALE_SECONDS:
        return None
    identity = _owner(owner)
    if identity is None:
        return None
    with _engine(engine).begin() as connection:
        row = connection.execute(text(
            'SELECT machine_id, base_url FROM anteroom_private.device_route(:owner, :device, :stale)'
        ), {'owner': identity, 'device': device, 'stale': STALE_SECONDS[device]}).first()
    if row is None:
        return None
    return DeviceRoute(row.machine_id, row.base_url, row.machine_id == machine_id())


def replay_instance(route: DeviceRoute | None) -> str | None:
    """The Fly machine that should receive this poll, when it is not the current one."""
    if route is None or route.owned or route.machine_id == machine_id():
        return None
    return route.machine_id


async def forward_target(owner: str | None, device: str) -> str | None:
    """Private URL of the process holding this device, or None to handle it here.

    The route lookup takes a pooled connection, so it runs on a worker thread:
    on the request loop, a full pool would stall every request while it waits.
    """
    if not owner or not routing_enabled():
        return None
    try:
        route = await asyncio.to_thread(lookup, owner, device)
    except SQLAlchemyError as exc:
        _warn_route('Device route could not be read', exc)
        if os.environ.get('ANTEROOM_PROCESS_ROLE') == 'worker':
            raise RoutingUnavailable from None
        return None
    if route is None or route.machine_id == machine_id():
        return None
    if not os.environ.get('ANTEROOM_PROCESS_TOKEN'):
        raise RoutingUnavailable
    if not _URL.match(route.base_url):
        raise RoutingUnavailable
    return route.base_url


def remote_fresh(owner: str | None, device: str) -> bool:
    if not owner or not routing_enabled():
        return False
    try:
        route = lookup(owner, device)
    except SQLAlchemyError:
        return False
    return route is not None and route.machine_id != machine_id()


def token_matches(presented: str) -> bool:
    expected = os.environ.get('ANTEROOM_PROCESS_TOKEN', '')
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


async def post(base_url: str, path: str, body: dict, timeout: float) -> dict:
    import httpx

    token = os.environ.get('ANTEROOM_PROCESS_TOKEN', '')
    if not token or not _URL.match(base_url):
        raise RoutingUnavailable
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                base_url + path, json=body, headers={'Authorization': f'Bearer {token}'},
            )
    except httpx.HTTPError:
        raise RoutingUnavailable from None
    if response.status_code != 200:
        raise RoutingUnavailable
    payload = response.json()
    if not isinstance(payload, dict):
        raise RoutingUnavailable
    return payload
