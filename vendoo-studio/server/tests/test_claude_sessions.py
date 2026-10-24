from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from claude_agent_sdk import ResultMessage, StreamEvent

from vendoo_studio.providers import claude_sessions
from vendoo_studio.providers.claude_agent import TEXT_ONLY_PREAMBLE, ClaudeProvider
from vendoo_studio.providers.xiaomi_mimo import unpack_stream_item
from vendoo_studio.services import claude_auth


def _result(text: str, *, is_error: bool = False) -> ResultMessage:
    return ResultMessage(
        subtype="error_during_execution" if is_error else "success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        result=text,
    )


def _delta(text: str) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    )


class _Client:
    def __init__(self, options, replies):
        self.options = options
        self.replies = replies
        self.sent: list = []
        self.models: list = []
        self.disconnected = False

    async def query(self, prompt):
        if isinstance(prompt, str):
            self.sent.append(prompt)
        else:
            self.sent.extend([turn async for turn in prompt])

    async def receive_response(self):
        if self.sent[-1] == "/clear":
            yield _result("")
            return
        for message in self.replies():
            await asyncio.sleep(0)
            yield message

    async def set_model(self, model):
        self.models.append(model)

    async def disconnect(self):
        self.disconnected = True


class _Spawner:
    def __init__(self, replies=lambda: [_delta("O"), _delta("K"), _result("OK")]):
        self.replies = replies
        self.clients: list[_Client] = []

    async def __call__(self, options):
        client = _Client(options, self.replies)
        self.clients.append(client)
        return client


def _run_warm(body):
    async def main():
        claude_sessions.keep_warm_on_this_loop()
        try:
            return await body()
        finally:
            await claude_sessions.close_warm_processes()

    return asyncio.run(main())


async def _settle():
    pool = claude_sessions._pool
    while pool is not None and pool.releasing:
        await asyncio.gather(*pool.releasing)
        await asyncio.sleep(0)  # done callbacks drop finished tasks from the set


class ClaudeSessionsTest(unittest.TestCase):
    def test_a_cancelled_call_closes_its_process(self):
        spawner = _Spawner(replies=lambda: [_delta("a"), _result("a")])
        started = asyncio.Event()

        async def body():
            async def hold():
                async with claude_sessions.session(("k",), lambda model: object(), None):
                    started.set()
                    await asyncio.sleep(10)

            with patch.object(claude_sessions, "_connect", spawner):
                task = asyncio.create_task(hold())
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await _settle()
            return claude_sessions._pool.live

        live = _run_warm(body)
        self.assertEqual(live, 0)
        self.assertTrue(spawner.clients[0].disconnected)

    def setUp(self):
        for patcher in (
            patch.object(claude_auth, "claude_cli_path", return_value="/usr/bin/claude"),
            patch("vendoo_studio.providers.claude_agent.get_claude_models", return_value={}),
            patch(
                "vendoo_studio.providers.claude_agent.resolved_claude_models",
                return_value=("haiku", "opus"),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _chat(self, provider, text="hi"):
        async def run():
            return [unpack_stream_item(chunk) async for chunk in provider.chat([{"role": "user", "content": text}])]

        return run()

    def test_calls_reuse_one_process_and_clear_it_in_between(self):
        spawner = _Spawner()

        async def body():
            provider = ClaudeProvider()
            with patch.object(claude_sessions, "_connect", spawner):
                first = await self._chat(provider)
                await _settle()
                second = await self._chat(provider)
                await _settle()
            return first, second

        first, second = _run_warm(body)
        self.assertEqual(first, [("content", "O"), ("content", "K")])
        self.assertEqual(second, first)
        self.assertEqual(len(spawner.clients), 1)
        client = spawner.clients[0]
        self.assertEqual([turn for turn in client.sent if isinstance(turn, str)], ["/clear", "/clear"])
        self.assertEqual(client.options.system_prompt, TEXT_ONLY_PREAMBLE)
        self.assertEqual(client.options.model, "opus")
        self.assertTrue(client.disconnected, "closing the pool closes idle processes")

    def test_a_new_model_switches_the_warm_process(self):
        spawner = _Spawner()

        async def body():
            provider = ClaudeProvider()
            with patch.object(claude_sessions, "_connect", spawner):
                await self._chat(provider)
                await _settle()
                async for _ in provider.vision_chat([{"role": "user", "content": "photo"}]):
                    pass
                await _settle()

        _run_warm(body)
        self.assertEqual(len(spawner.clients), 1)
        self.assertEqual(spawner.clients[0].models, ["haiku"])

    def test_a_failed_call_closes_its_process(self):
        spawner = _Spawner(replies=lambda: [_result("boom", is_error=True)])

        async def body():
            provider = ClaudeProvider()
            with patch.object(claude_sessions, "_connect", spawner):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    await self._chat(provider)
                await _settle()
                return claude_sessions._pool.live

        live = _run_warm(body)
        self.assertEqual(live, 0)
        self.assertTrue(spawner.clients[0].disconnected)

    def test_a_stream_stopped_early_closes_its_process(self):
        spawner = _Spawner(replies=lambda: [_delta("a"), _delta("b"), _result("ab")])

        async def body():
            provider = ClaudeProvider()
            with patch.object(claude_sessions, "_connect", spawner):
                stream = provider.chat([{"role": "user", "content": "hi"}])
                async for _ in stream:
                    break
                await stream.aclose()
                # The inner generator is finalized by the loop a tick later.
                for _ in range(5):
                    await asyncio.sleep(0)
                await _settle()
                return claude_sessions._pool.live

        live = _run_warm(body)
        self.assertEqual(live, 0)
        self.assertTrue(spawner.clients[0].disconnected)
        self.assertNotIn("/clear", spawner.clients[0].sent)

    def test_calls_past_the_cap_wait_for_a_free_process(self):
        spawner = _Spawner()
        peak = 0

        async def body():
            nonlocal peak
            gate = asyncio.Event()

            async def hold():
                nonlocal peak
                async with claude_sessions.session(("k",), lambda model: object(), None):
                    peak = max(peak, claude_sessions._pool.live)
                    await gate.wait()

            with patch.object(claude_sessions, "_connect", spawner), patch.object(claude_sessions, "MAX_PROCESSES", 2):
                tasks = [asyncio.create_task(hold()) for _ in range(3)]
                for _ in range(10):
                    await asyncio.sleep(0)
                started = len(spawner.clients)
                gate.set()
                await asyncio.gather(*tasks)
                await _settle()
            return started

        started = _run_warm(body)
        self.assertEqual(started, 2)
        self.assertEqual(peak, 2)
        self.assertEqual(len(spawner.clients), 2, "the third call reused a freed process")

    def test_an_idle_process_of_another_kind_makes_room_at_the_cap(self):
        spawner = _Spawner()

        async def body():
            with patch.object(claude_sessions, "_connect", spawner), patch.object(claude_sessions, "MAX_PROCESSES", 1):
                async with claude_sessions.session(("text",), lambda model: object(), None):
                    pass
                await _settle()
                async with claude_sessions.session(("web",), lambda model: object(), None):
                    pass
                await _settle()
            return claude_sessions._pool.live

        live = _run_warm(body)
        self.assertEqual(len(spawner.clients), 2)
        self.assertTrue(spawner.clients[0].disconnected)
        self.assertEqual(live, 1)

    def test_idle_processes_close_after_a_while(self):
        spawner = _Spawner()

        async def body():
            with patch.object(claude_sessions, "_connect", spawner), patch.object(claude_sessions, "IDLE_CLOSE_SEC", 0.01):
                async with claude_sessions.session(("k",), lambda model: object(), None):
                    pass
                await _settle()
                await asyncio.sleep(0.05)
                pool = claude_sessions._pool
                if pool.closing:
                    await asyncio.gather(*pool.closing)
                return pool.live

        live = _run_warm(body)
        self.assertEqual(live, 0)
        self.assertTrue(spawner.clients[0].disconnected)

    def test_off_the_server_loop_a_call_gets_its_own_process(self):
        spawner = _Spawner()
        claude_sessions._pool = None

        async def body():
            provider = ClaudeProvider()
            with patch.object(claude_sessions, "_connect", spawner):
                await self._chat(provider)
                await self._chat(provider)

        asyncio.run(body())
        self.assertEqual(len(spawner.clients), 2)
        self.assertTrue(all(client.disconnected for client in spawner.clients))
        self.assertTrue(all("/clear" not in client.sent for client in spawner.clients))


if __name__ == "__main__":
    unittest.main()
