"""The connector's trusted native API. No listing server or database is started."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import uuid
import webbrowser

from vendoo_studio.services.mac_ai_worker import MacAIAPI


class ConnectorAPI(MacAIAPI):
    def __init__(self, window, worker=None):
        super().__init__(window, worker)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name='connector-sign-in')
        self._thread.start()
        self._terminal = None
        self._terminal_id = None
        self._shutdown_guard = threading.Lock()
        self._stopped = False

    def _require_trusted(self):
        if not self._trusted_origin():
            raise PermissionError('Open the connector connection window to use this action.')

    def _call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=45)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise RuntimeError('The connection did not answer. Try again.') from None
        except ValueError:
            raise
        except Exception:
            raise RuntimeError('Could not complete this connection action. Try again.') from None

    def connector_status(self):
        self._require_trusted()
        return self._call(self._status())

    async def _status(self):
        from vendoo_studio.services import chatgpt_oauth, claude_auth
        from vendoo_studio.providers.chatgpt_codex import resolved_chatgpt_models
        from vendoo_studio.services.user_settings import resolved_claude_models
        chatgpt, claude = await asyncio.gather(
            asyncio.to_thread(chatgpt_oauth.chatgpt_signed_in), asyncio.to_thread(claude_auth.status),
        )
        gpt_vision, gpt_listing = resolved_chatgpt_models()
        claude_vision, claude_listing = resolved_claude_models()
        return {
            **self._worker.status(),
            'chatgpt': {'signed_in': chatgpt, 'pending': chatgpt_oauth.pending_login(),
                        'error': chatgpt_oauth.login_error(), 'vision_model': gpt_vision, 'listing_model': gpt_listing},
            'claude': {**claude, 'vision_model': claude_vision, 'listing_model': claude_listing},
        }

    def connector_pause(self, paused):
        self._require_trusted()
        if not isinstance(paused, bool):
            raise ValueError('Choose Pause or Resume.')
        self._worker.set_paused(paused)
        return {'ok': True}

    def connector_chatgpt_login(self):
        self._require_trusted()
        from vendoo_studio.services.chatgpt_oauth import start_login
        return self._call(start_login())

    def connector_chatgpt_logout(self):
        self._require_trusted()
        return self._call(self._logout_chatgpt())

    async def _logout_chatgpt(self):
        from vendoo_studio.services.chatgpt_oauth import cancel_login, logout
        await cancel_login()
        await logout()
        return {'ok': True}

    def connector_models(self, provider):
        self._require_trusted()
        if provider not in ('chatgpt', 'claude'):
            raise ValueError('Choose ChatGPT or Claude.')
        return self._call(self._models(provider))

    async def _models(self, provider):
        if provider == 'chatgpt':
            from vendoo_studio.providers.chatgpt_codex import fetch_chatgpt_models
            return [{'value': model, 'label': model} for model in await fetch_chatgpt_models()]
        from vendoo_studio.providers.claude_agent import list_models
        return await list_models()

    def connector_set_model(self, provider, model):
        self._require_trusted()
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ValueError('Choose a model from this subscription.')
        if provider == 'chatgpt':
            from vendoo_studio.services.keychain import set_chatgpt_models
            set_chatgpt_models(vision_model=model, listing_model=model)
        elif provider == 'claude':
            from vendoo_studio.services.user_settings import set_claude_models
            set_claude_models(vision_model=model, listing_model=model)
        else:
            raise ValueError('Choose ChatGPT or Claude.')
        return {'ok': True}

    def connector_setup_claude(self):
        self._require_trusted()
        webbrowser.open('https://code.claude.com/docs/en/setup')
        return {'ok': True}

    def connector_claude_start(self, cols, rows):
        self._require_trusted()
        cols, rows = self._dimensions(cols, rows)
        return self._call(self._start_terminal(cols, rows))

    @staticmethod
    def _dimensions(cols, rows):
        if not isinstance(cols, int) or not isinstance(rows, int) or not 20 <= cols <= 400 or not 1 <= rows <= 100:
            raise ValueError('Invalid terminal dimensions.')
        return cols, rows

    async def _start_terminal(self, cols, rows):
        from vendoo_studio.services.claude_auth import LoginTerminal
        if self._terminal is not None:
            await self._close_terminal()
        self._terminal = LoginTerminal(cols, rows)
        self._terminal_id = uuid.uuid4().hex
        return {'id': self._terminal_id}

    def _require_terminal(self, identity):
        self._require_trusted()
        terminal = self._terminal
        if terminal is None or identity != self._terminal_id:
            raise ValueError('This Claude sign-in has ended.')
        return terminal

    def connector_claude_read(self, identity):
        self._require_terminal(identity)
        return self._call(self._read_terminal(identity))

    async def _read_terminal(self, identity):
        terminal = self._require_terminal(identity)
        try:
            data = await asyncio.wait_for(terminal.read(), timeout=10)
        except TimeoutError:
            return {'waiting': True}
        if data is not None:
            return {'data': list(data)}
        code = await terminal.close()
        if self._terminal is terminal:
            self._terminal = self._terminal_id = None
        return {'code': code}

    def connector_claude_send(self, identity, message):
        self._require_terminal(identity)
        return self._call(self._send_terminal(identity, message))

    async def _send_terminal(self, identity, message):
        terminal = self._require_terminal(identity)
        if not isinstance(message, dict):
            raise ValueError('Invalid terminal input.')
        if message.get('type') == 'input':
            data = message.get('data')
            if not isinstance(data, str) or len(data) > 8192:
                raise ValueError('Invalid terminal input.')
            terminal.write(data.encode())
        elif message.get('type') == 'resize':
            terminal.resize(*self._dimensions(message.get('cols'), message.get('rows')))
        else:
            raise ValueError('Invalid terminal input.')
        return {'ok': True}

    def connector_claude_close(self, identity):
        self._require_trusted()
        return self._call(self._close_terminal(identity))

    async def _close_terminal(self, identity=None):
        if self._terminal is not None and (identity is None or identity == self._terminal_id):
            terminal, self._terminal = self._terminal, None
            self._terminal_id = None
            await terminal.close()
        return {'ok': True}

    async def _cleanup(self):
        from vendoo_studio.services.chatgpt_oauth import cancel_login
        await cancel_login()
        await self._close_terminal()
        await asyncio.to_thread(self._worker.close)
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _shutdown(self):
        with self._shutdown_guard:
            if self._stopped:
                return
            self._stopped = True
        try:
            self._call(self._cleanup())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=3)
            if not self._thread.is_alive():
                self._loop.close()
