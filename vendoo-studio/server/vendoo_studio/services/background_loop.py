"""An event loop thread for work that must stay independent of blocking callers."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading

# asyncio sizes the pool behind ``asyncio.to_thread`` from the CPU count: five
# threads on the one-vCPU hosted Machine. Every database step of an async route
# and every photo copy to Storage shares it, so a few slow copies stalled every
# other request. Give each loop the 40 threads FastAPI gives plain ``def`` routes.
LOOP_THREADS = 40


def give_threads(loop: asyncio.AbstractEventLoop) -> None:
    """Size ``loop``'s ``asyncio.to_thread`` pool for waiting on I/O, not for the CPU count."""
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(
        max_workers=LOOP_THREADS, thread_name_prefix="anteroom-loop"))


class BackgroundLoop:
    def __init__(self, name: str):
        self.loop = asyncio.new_event_loop()
        give_threads(self.loop)
        self.thread = threading.Thread(target=self.loop.run_forever, name=name, daemon=True)
        self.thread.start()

    def run(self, coroutine) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def stop(self) -> None:
        async def cancel_all():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(cancel_all(), self.loop).result(timeout=10)
        except concurrent.futures.TimeoutError:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        if not self.thread.is_alive():
            self.loop.close()
