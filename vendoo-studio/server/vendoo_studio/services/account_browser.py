"""Private, in-memory account windows for read-only marketplace metadata.

Only domain clients supply scripts; no endpoint or script is accepted from a route.
Remote pages receive no Python API and their cookies never leave the webview.
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlparse


class AccountConnectionError(RuntimeError):
    pass


class PrivateAccountWindow:
    def __init__(self, name: str, origin: str, connect_path: str):
        self.name, self.origin, self.connect_path = name, origin, connect_path
        self._factory = None
        self._window = None
        self._lock = threading.RLock()

    def install(self, factory) -> None:
        self._factory = factory

    def shutdown(self, *_) -> None:
        with self._lock:
            window, self._window = self._window, None
        if window is not None:
            window.destroy()

    def connect(self) -> dict:
        with self._lock:
            if self._factory is None:
                raise AccountConnectionError(f'Open the Anteroom Mac app to sign in to {self.name} without Chrome.')
            if self._window is None:
                window = self._factory(f'Sign in to {self.name}', self.origin + self.connect_path,
                                       js_api=None, width=600, height=800)
                self._window = window

                def closing(*_):
                    with self._lock:
                        keep_session = self._window is window
                    if keep_session:
                        window.hide()
                        return False
                    return True

                def closed(*_):
                    with self._lock:
                        if self._window is window:
                            self._window = None
                window.events.closing += closing
                window.events.closed += closed
            else:
                self._window.show()
        return {'ok': True}

    def read(self, path: str, extract: str, *, headers: str = "{Accept:'application/json'}") -> list:
        """Execute a fixed metadata GET and return only the client's selected fields."""
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Account metadata requests must stay on their marketplace.')
        with self._lock:
            window = self._window
        if window is None:
            raise AccountConnectionError(f'Sign in to {self.name} in Anteroom, then reload its choices.')
        try:
            url = urlparse(window.get_current_url() or '')
        except Exception:
            raise AccountConnectionError(f'Reopen the {self.name} sign-in window in Anteroom.') from None
        expected = urlparse(self.origin)
        if url.scheme != expected.scheme or url.netloc != expected.netloc:
            raise AccountConnectionError(f'Finish signing in to {self.name}, then reload its choices.')
        finished = threading.Event()
        result = []

        def done(value):
            result.append(value)
            finished.set()

        script = r"""(async () => {
          if (location.origin !== ORIGIN) return {ok:false};
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), 15000);
          try {
            const headers = HEADERS;
            const response = await fetch(PATH, {method:'GET', credentials:'same-origin', headers, signal:controller.signal});
            if (!response.ok || new URL(response.url).origin !== location.origin) return {ok:false};
            const body = await response.json();
            const rows = EXTRACT;
            return Array.isArray(rows) ? {ok:true, rows} : {ok:false};
          } catch (_) { return {ok:false}; } finally { clearTimeout(timeout); }
        })()""".replace('ORIGIN', json.dumps(self.origin)).replace('PATH', json.dumps(path)).replace('HEADERS', headers).replace('EXTRACT', extract)
        error = f'{self.name} choices could not be read. Finish signing in and reload them.'
        try:
            window.evaluate_js(script, done)
        except Exception:
            raise AccountConnectionError(error) from None
        if not finished.wait(20) or not result or not isinstance(result[0], dict) or result[0].get('ok') is not True:
            raise AccountConnectionError(error)
        rows = result[0].get('rows')
        if not isinstance(rows, list):
            raise AccountConnectionError(f'{self.name} returned unrecognised choices.')
        try:
            window.hide()
        except Exception:
            pass
        return rows
