#!/usr/bin/env python3
"""Measure hosted page and save latency while several sellers generate drafts.

Runs against the local hosted sandbox only (``./sandbox.sh --hosted``, or the
API alone, see "Hosted load test" in vendoo-studio/README.md). It refuses any
server that is not a loopback sandbox, so it never touches production.

Each seller signs in, gets drafts with photos to generate, drafts to edit and
some extra inventory. Then two phases run for the same length of time:

- idle: every seller's tabs browse and save (inventory, open a draft, save a
  listing field and the notes, suggestions, analytics, sales, status);
- load: the same browsing while each seller keeps several drafts generating,
  each followed by a second open stream, and restarts a generation when one
  finishes.

It reports p50/p95/max per request type and phase, HTTP errors, generation
acknowledgement and duration, database connections and transactions per second
(with ``--database-url``), process CPU (with ``--pid``) and the host's load
average. ``--out`` writes every sample as JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import io
import json
import os
import random
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw

SELLERS = ['alice', 'bob'] + [f'seller{number}' for number in range(3, 51)]
CLIENT_DEADLINE = 30.0


@dataclass
class Results:
    phase: str = 'setup'
    samples: list[dict] = field(default_factory=list)
    generations: list[dict] = field(default_factory=list)
    system: list[dict] = field(default_factory=list)
    holders: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    holder_samples: Counter = field(default_factory=Counter)

    def add(self, kind: str, started: float, headers: float | None, status: int | str):
        done = time.perf_counter()
        self.samples.append({'phase': self.phase, 'kind': kind, 'status': status, 'at': time.time(),
                             'ms': round((done - started) * 1000, 1),
                             'headers_ms': round((headers - started) * 1000, 1) if headers else None})


def photo(label: str) -> bytes:
    image = Image.new('RGB', (800, 1000), (random.randrange(256), random.randrange(256), random.randrange(256)))
    ImageDraw.Draw(image).text((60, 60), label, fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, 'JPEG', quality=80)
    return buffer.getvalue()


async def timed(results: Results, client: httpx.AsyncClient, kind: str, method: str, url: str, **kwargs):
    started = time.perf_counter()
    try:
        async with client.stream(method, url, **kwargs) as response:
            headers = time.perf_counter()
            body = await response.aread()
    except httpx.TimeoutException:
        results.add(kind, started, None, 'timeout')
        return None
    except httpx.HTTPError as exc:
        results.add(kind, started, None, type(exc).__name__)
        return None
    results.add(kind, started, headers, response.status_code)
    if response.status_code >= 400:
        return None
    return json.loads(body) if body else None


class Seller:
    def __init__(self, base_url: str, name: str, args):
        self.base_url, self.name, self.args = base_url, name, args
        self.generate_ids: list[str] = []
        self.edit_ids: list[str] = []

    def client(self, timeout: float = CLIENT_DEADLINE) -> httpx.AsyncClient:
        # One connection pool per tab, the way separate browser tabs and devices connect.
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, headers=self.headers,
                                 limits=httpx.Limits(max_connections=20))

    async def sign_in(self):
        async with httpx.AsyncClient(base_url=self.base_url, timeout=CLIENT_DEADLINE) as client:
            response = await client.post('/local/auth/v1/token?grant_type=password',
                                         json={'email': f'{self.name}@sandbox.test', 'password': 'sandbox'})
            response.raise_for_status()
            self.headers = {'Authorization': 'Bearer ' + response.json()['access_token']}

    async def setup(self):
        async with self.client(120) as client:
            async def draft(title: str, fields: dict | None, with_photo: bool) -> str:
                created = (await client.post('/api/drafts', json={'title': title})).raise_for_status().json()
                if with_photo:
                    (await client.post(f"/api/drafts/{created['id']}/photos", content=photo(title),
                                       headers={'Content-Type': 'image/jpeg'})).raise_for_status()
                if fields:
                    (await client.patch(f"/api/drafts/{created['id']}", json={
                        'title': title, 'notes': '', 'expected_updated_at': created['updated_at'], 'fields': fields,
                    })).raise_for_status()
                return created['id']

            listing = {'description': 'Load test item.', 'price': 25, 'brand': "Levi's", 'size': 'M',
                       'condition': 'Good', 'primaryColor': 'Blue', 'quantity': 1,
                       'category_path': "Clothing, Shoes & Accessories > Men > Men's Clothing > Shirts > T-Shirts"}
            for number in range(self.args.generating):
                self.generate_ids.append(await draft(f'{self.name} generate {number}', None, True))
            for number in range(self.args.tabs):
                self.edit_ids.append(await draft(f'{self.name} edit {number}', listing, True))
            for number in range(self.args.inventory):
                await draft(f'{self.name} inventory {number}', {**listing, 'price': 10 + number}, False)

    async def browse(self, results: Results, tab: int, stop: asyncio.Event):
        """One tab's normal activity, paced by --think seconds between requests."""
        edit_id = self.edit_ids[tab]
        revision = None
        price = 25
        async with self.client() as client:
            async def pause():
                try:
                    await asyncio.wait_for(stop.wait(), self.args.think * random.uniform(0.5, 1.5))
                except TimeoutError:
                    pass

            while not stop.is_set():
                await timed(results, client, 'inventory', 'GET', '/api/conversations')
                await pause()
                screen = await timed(results, client, 'screen', 'GET', f'/api/conversations/{edit_id}/screen')
                if screen:
                    revision = screen['listing']['current_revision_id']
                await pause()
                if revision is not None and screen:
                    price = 26 if price == 25 else 25
                    saved = await timed(results, client, 'save_field', 'PUT', f'/api/conversations/{edit_id}/listing',
                                        json={'listing': {**screen['listing']['listing'], 'price': price},
                                              'expected_revision_id': revision})
                    revision = saved['revision_id'] if saved else None
                await pause()
                await timed(results, client, 'save_notes', 'PATCH', f'/api/conversations/{edit_id}',
                            json={'notes': f'Load test note {time.time():.3f}'})
                await pause()
                if self.generate_ids:
                    await timed(results, client, 'screen_generating', 'GET',
                                f'/api/conversations/{random.choice(self.generate_ids)}/screen')
                    await pause()
                await timed(results, client, 'suggestions', 'GET', '/api/suggestions')
                await pause()
                await timed(results, client, 'analytics', 'GET', '/api/analytics')
                await pause()
                today = datetime.date.today()
                await timed(results, client, 'sales', 'GET', '/api/sales-ledger', params={
                    'start': (today - datetime.timedelta(days=90)).isoformat(), 'end': today.isoformat()})
                await pause()
                await timed(results, client, 'status', 'GET', '/api/status')
                await pause()

    async def generate(self, results: Results, draft_id: str, stop: asyncio.Event):
        """Keep one draft generating until stop, with a second tab following each run."""
        async with self.client(self.args.generation_deadline) as client, \
                self.client(self.args.generation_deadline) as follower:
            while not stop.is_set():
                record = {'seller': self.name, 'draft': draft_id, 'started': time.time()}
                started = time.perf_counter()
                following = None
                try:
                    async with client.stream('POST', f'/api/conversations/{draft_id}/generate') as response:
                        record['ack_ms'] = round((time.perf_counter() - started) * 1000, 1)
                        record['status'] = response.status_code
                        if response.status_code == 200:
                            following = asyncio.create_task(self.follow(follower, draft_id))
                            await self.read_stream(response, record, started)
                        else:
                            await response.aread()
                except httpx.HTTPError as exc:
                    record['status'] = type(exc).__name__
                record['total_s'] = round(time.perf_counter() - started, 2)
                if stop.is_set() and record.get('outcome') == 'done':
                    # Still running when the phase ended; the cancel closed its stream.
                    record['outcome'] = 'cancelled'
                if following is not None:
                    record['follower'] = await following
                results.generations.append(record)
                if record.get('status') != 200:
                    await asyncio.sleep(1)

    @staticmethod
    async def read_stream(response, record: dict, started: float):
        event = None
        async for line in response.aiter_lines():
            if line.startswith('event: '):
                event = line[7:]
            elif line.startswith('data: '):
                if 'first_event_ms' not in record:
                    record['first_event_ms'] = round((time.perf_counter() - started) * 1000, 1)
                if line == 'data: [DONE]':
                    record['outcome'] = record.get('outcome', 'done')
                    return
                if event == 'error':
                    record['outcome'] = 'error'
            elif not line:
                event = None
        record['outcome'] = record.get('outcome', 'closed')

    async def follow(self, client: httpx.AsyncClient, draft_id: str) -> dict:
        """A second tab or device opening the same generation's stream."""
        record: dict = {}
        started = time.perf_counter()
        try:
            async with client.stream('POST', f'/api/conversations/{draft_id}/generate?resume=true') as response:
                record['ack_ms'] = round((time.perf_counter() - started) * 1000, 1)
                record['status'] = response.status_code
                await self.read_stream(response, record, started)
        except httpx.HTTPError as exc:
            record['status'] = type(exc).__name__
        return record

    async def cancel_all(self):
        async with self.client() as client:
            for draft_id in self.generate_ids:
                await client.post(f'/api/conversations/{draft_id}/generate/cancel')


def cpu_seconds(pid: int) -> float | None:
    try:
        with open(f'/proc/{pid}/stat') as handle:
            fields = handle.read().rsplit(')', 1)[1].split()
    except OSError:
        return None
    return (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')


async def sample_system(results: Results, args, stop: asyncio.Event):
    """Once a second: load average, process CPU and the database's connections and transactions."""
    connection = None
    if args.database_url:
        import psycopg
        connection = await psycopg.AsyncConnection.connect(args.database_url, autocommit=True)
    pids = dict(item.split('=', 1) for item in args.pid)
    last_cpu = {name: cpu_seconds(int(pid)) for name, pid in pids.items()}
    last_xact = None
    last_at = time.perf_counter()
    try:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass
            now = time.perf_counter()
            elapsed, last_at = now - last_at, now
            row: dict = {'phase': results.phase, 'at': time.time(), 'load1': os.getloadavg()[0]}
            for name, pid in pids.items():
                cpu = cpu_seconds(int(pid))
                if cpu is not None and last_cpu.get(name) is not None:
                    row[f'cpu_{name}'] = round((cpu - last_cpu[name]) / elapsed, 3)
                last_cpu[name] = cpu
            if connection is not None:
                cursor = await connection.execute(
                    "SELECT count(*) FILTER (WHERE state = 'active'), count(*) FILTER (WHERE state = 'idle in transaction'),"
                    " count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()")
                row['db_active'], row['db_idle_in_tx'], row['db_connections'] = await cursor.fetchone()
                cursor = await connection.execute(
                    'SELECT sum(xact_commit + xact_rollback) FROM pg_stat_database')
                xact = int((await cursor.fetchone())[0])
                if last_xact is not None:
                    # Includes this sampler's own two statements a second.
                    row['db_xact_per_s'] = round((xact - last_xact) / elapsed, 1)
                last_xact = xact
            results.system.append(row)
    finally:
        if connection is not None:
            await connection.close()


def statement_label(query: str) -> str:
    """'SELECT background_task_events', 'UPDATE conversations': the verb and first table, never values."""
    words = query.split()
    verb = words[0].upper() if words else '?'
    match = re.search(r'\b(?:FROM|INTO|UPDATE)\s+"?([a-z_][a-z0-9_.]*)', query, re.IGNORECASE)
    if 'set_config' in query:
        return 'SELECT set_config (seller transaction start)'
    return f'{verb} {match.group(1)}' if match else verb


async def sample_holders(results: Results, args, stop: asyncio.Event):
    """Four times a second, what each connection inside a transaction last ran.

    A pooled connection is held from checkout to commit; between round trips
    Postgres shows it ``idle in transaction``. Counting those by statement
    shows which code paths keep connections out of the pool.
    """
    import psycopg
    async with await psycopg.AsyncConnection.connect(args.database_url, autocommit=True) as connection:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), .25)
            except TimeoutError:
                pass
            cursor = await connection.execute(
                "SELECT query FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()"
                " AND state IN ('active', 'idle in transaction')")
            for (query,) in await cursor.fetchall():
                results.holders[results.phase][statement_label(query or '')] += 1
            results.holder_samples[results.phase] += 1


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def report(results: Results, args) -> str:
    lines = [f'Load: {args.sellers} sellers, {args.tabs} browsing tabs and {args.generating} generating drafts each'
             f' (+1 follower stream per generation), think {args.think}s, {args.seconds}s per phase.', '']
    lines.append(f"{'request':<18} {'phase':<5} {'n':>5} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} {'errors':>7}")
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sample in results.samples:
        grouped[(sample['kind'], sample['phase'])].append(sample)
    for (kind, phase), samples in sorted(grouped.items()):
        good = [sample['ms'] for sample in samples if isinstance(sample['status'], int) and sample['status'] < 400]
        errors = len(samples) - len(good)
        if good:
            lines.append(f'{kind:<18} {phase:<5} {len(samples):>5} {percentile(good, .5):>8.0f} '
                         f'{percentile(good, .95):>8.0f} {max(good):>8.0f} {errors:>7}')
        else:
            lines.append(f'{kind:<18} {phase:<5} {len(samples):>5} {"-":>8} {"-":>8} {"-":>8} {errors:>7}')
    statuses = defaultdict(int)
    for sample in results.samples:
        if not (isinstance(sample['status'], int) and sample['status'] < 400):
            statuses[(sample['phase'], sample['kind'], sample['status'])] += 1
    if statuses:
        lines += ['', 'Errors (phase, request, status: count):']
        lines += [f'  {phase} {kind} {status}: {count}' for (phase, kind, status), count in sorted(statuses.items(), key=str)]
    def count(outcome):
        return sum(1 for g in results.generations if g.get('outcome') == outcome)

    finished = [g for g in results.generations if g.get('outcome') == 'done']
    lines += ['', f'Generations: {len(results.generations)} started, {len(finished)} finished,'
              f" {count('cancelled')} still running at the end (cancelled), {count('error')} ended in an error event,"
              f" {sum(1 for g in results.generations if g.get('status') != 200)} refused or failed to connect."]
    ack = [g['ack_ms'] for g in results.generations if 'ack_ms' in g]
    if ack:
        first = [g['first_event_ms'] for g in results.generations if 'first_event_ms' in g] or [0]
        follower = [g['follower']['ack_ms'] for g in results.generations if g.get('follower', {}).get('ack_ms')] or [0]
        lines.append(f'  submit ack p50/p95/max {percentile(ack, .5):.0f}/{percentile(ack, .95):.0f}/{max(ack):.0f} ms;'
                     f' follower stream ack p50/p95 {percentile(follower, .5):.0f}/{percentile(follower, .95):.0f} ms;'
                     f' first progress p50/p95 {percentile(first, .5):.0f}/{percentile(first, .95):.0f} ms')
    if finished:
        durations = [g['total_s'] for g in finished]
        lines.append(f'  finished in p50/p95 {percentile(durations, .5):.1f}/{percentile(durations, .95):.1f} s')
    lines += ['', 'System (phase: mean / max):']
    keys = sorted({key for row in results.system for key in row} - {'phase', 'at'})
    for phase in ('idle', 'load'):
        rows = [row for row in results.system if row['phase'] == phase]
        parts = []
        for key in keys:
            values = [row[key] for row in rows if key in row]
            if values:
                parts.append(f'{key} {statistics.fmean(values):.2f}/{max(values):.2f}')
        if parts:
            lines.append(f'  {phase}: ' + ', '.join(parts))
    for phase in ('idle', 'load'):
        if results.holder_samples[phase]:
            lines += ['', f'Connections inside a transaction, {phase} (mean held at once, by last statement):']
            lines += [f'  {count / results.holder_samples[phase]:5.2f}  {label}'
                      for label, count in results.holders[phase].most_common(8)]
    return '\n'.join(lines)


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base-url', default='http://127.0.0.1:4320')
    parser.add_argument('--sellers', type=int, default=2, help='sellers signed in (the sandbox needs ANTEROOM_SANDBOX_SELLERS >= this)')
    parser.add_argument('--tabs', type=int, default=2, help='browsing tabs per seller, each editing its own draft')
    parser.add_argument('--generating', type=int, default=3, help='drafts per seller kept generating in the load phase')
    parser.add_argument('--inventory', type=int, default=20, help='extra saved drafts per seller')
    parser.add_argument('--seconds', type=float, default=60, help='length of each phase')
    parser.add_argument('--think', type=float, default=0.5, help='mean pause between one tab\'s requests')
    parser.add_argument('--generation-deadline', type=float, default=600)
    parser.add_argument('--database-url', help="the sandbox's Postgres, printed by the sandbox at startup")
    parser.add_argument('--pid', action='append', default=[], metavar='NAME=PID', help='a process to sample CPU for')
    parser.add_argument('--out', help='write every sample here as JSON')
    args = parser.parse_args()

    host = urlsplit(args.base_url).hostname
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Load tests run only against a loopback sandbox')
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as client:
        health = (await client.get('/api/health')).json()
    if health.get('sandbox') is not True:
        raise SystemExit(f'{args.base_url} is not a hosted sandbox; refusing to load it')
    if args.sellers > len(SELLERS):
        raise SystemExit(f'At most {len(SELLERS)} sellers')

    sellers = [Seller(args.base_url, name, args) for name in SELLERS[:args.sellers]]
    await asyncio.gather(*(seller.sign_in() for seller in sellers))
    print(f'Preparing {len(sellers)} sellers...', flush=True)
    await asyncio.gather(*(seller.setup() for seller in sellers))

    results = Results()
    sampling = asyncio.Event()
    samplers = [asyncio.create_task(sample_system(results, args, sampling))]
    if args.database_url:
        samplers.append(asyncio.create_task(sample_holders(results, args, sampling)))
    for phase in ('idle', 'load'):
        results.phase = phase
        print(f'{phase} phase: {args.seconds:.0f}s', flush=True)
        stop = asyncio.Event()
        tasks = [asyncio.create_task(seller.browse(results, tab, stop)) for seller in sellers for tab in range(args.tabs)]
        if phase == 'load':
            tasks += [asyncio.create_task(seller.generate(results, draft_id, stop))
                      for seller in sellers for draft_id in seller.generate_ids]
        await asyncio.sleep(args.seconds)
        stop.set()
        browsing = tasks[:len(sellers) * args.tabs]
        await asyncio.gather(*browsing)
        if phase == 'load':
            # Generations still running are cancelled; their streams then end.
            results.phase = 'end'
            await asyncio.gather(*(seller.cancel_all() for seller in sellers))
            await asyncio.gather(*tasks[len(browsing):])
    sampling.set()
    await asyncio.gather(*samplers)

    text = report(results, args)
    print(text)
    if args.out:
        with open(args.out, 'w') as handle:
            json.dump({'args': vars(args), 'samples': results.samples, 'generations': results.generations,
                       'system': results.system, 'holders': results.holders, 'report': text}, handle, indent=1)


if __name__ == '__main__':
    asyncio.run(main())
