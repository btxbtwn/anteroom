"""Read Vinted metadata in Anteroom's own private webview, never in Chrome.

The webview owns its in-memory cookies. No Python API is exposed to Vinted,
no credential crosses into the editor, and only catalog GETs are permitted.
"""
from __future__ import annotations

import json
import threading
from urllib.parse import urlencode, urlparse

ORIGIN = 'https://www.vinted.com'
_factory = None
_window = None
_lock = threading.RLock()


class VintedConnectionError(RuntimeError):
    pass


def install(factory) -> None:
    global _factory
    _factory = factory


def shutdown(*_) -> None:
    global _window
    with _lock:
        window, _window = _window, None
    if window is not None:
        window.destroy()


def connect() -> dict:
    global _window
    with _lock:
        if _factory is None:
            raise VintedConnectionError('Open the Anteroom Mac app to sign in to Vinted without Chrome.')
        if _window is None:
            window = _factory('Sign in to Vinted', ORIGIN + '/items/new', js_api=None, width=600, height=800)
            _window = window

            def closing(*_):
                with _lock:
                    keep_session = _window is window
                if keep_session:
                    window.hide()
                    return False
                return True

            def closed(*_):
                global _window
                with _lock:
                    if _window is window:
                        _window = None
            window.events.closing += closing
            window.events.closed += closed
        else:
            _window.show()
    return {'ok': True}


def _read(path: str, result_key: str):
    if (path, result_key) != ('/api/v2/item_upload/catalogs', 'catalogs') and not (
        path.startswith('/api/v2/item_upload/brands?') and result_key == 'brands'
    ):
        raise ValueError('This connection only reads Vinted categories and brands.')
    with _lock:
        window = _window
    if window is None:
        raise VintedConnectionError('Sign in to Vinted in Anteroom, then reload its choices.')
    url = urlparse(window.get_current_url() or '')
    if url.scheme != 'https' or url.netloc != 'www.vinted.com':
        raise VintedConnectionError('Finish signing in to Vinted, then reload its choices.')
    finished = threading.Event()
    result = []

    def done(value):
        result.append(value)
        finished.set()

    script = r'''(async () => {
      if (location.origin !== ORIGIN) return {ok:false};
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        const headers = {Accept:'application/json', Locale:'en-US', 'x-enable-multiple-size-groups':'true'};
        const csrf = document.documentElement.innerHTML.match(/\\?"CSRF_TOKEN\\?"\s*:\s*\\?"([^"\\]+)\\?"/);
        if (csrf && csrf[1].length <= 200 && !/\s/.test(csrf[1])) headers['x-csrf-token'] = csrf[1];
        const anon = document.cookie.match(/(?:^|;\s*)anon_id=([^;]+)/);
        if (anon) headers['x-anon-id'] = decodeURIComponent(anon[1]);
        const response = await fetch(PATH, {method:'GET', credentials:'same-origin', headers, signal:controller.signal});
        if (!response.ok) return {ok:false};
        const body = await response.json();
        return Array.isArray(body[KEY]) ? {ok:true, rows:body[KEY]} : {ok:false};
      } catch (_) { return {ok:false}; } finally { clearTimeout(timeout); }
    })()'''.replace('ORIGIN', json.dumps(ORIGIN)).replace('PATH', json.dumps(path)).replace('KEY', json.dumps(result_key))
    try:
        window.evaluate_js(script, done)
    except Exception:
        raise VintedConnectionError('Vinted choices could not be read. Finish signing in and reload them.') from None
    if not finished.wait(20) or not result or not isinstance(result[0], dict) or result[0].get('ok') is not True:
        raise VintedConnectionError('Vinted choices could not be read. Finish signing in and reload them.')
    rows = result[0].get('rows')
    if not isinstance(rows, list):
        raise VintedConnectionError('Vinted returned an unrecognised catalog.')
    try:
        window.hide()
    except Exception:
        pass
    return rows


def category_rows() -> list[dict]:
    return _read('/api/v2/item_upload/catalogs', 'catalogs')


def category_options(rows: list[dict], trail: tuple[str, ...] = ()) -> list[str]:
    options = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get('title') or '').strip()
        if not title:
            continue
        path = (*trail, title)
        children = row.get('catalogs') or row.get('children') or []
        if children and isinstance(children, list):
            options.extend(category_options(children, path))
        else:
            options.append(' > '.join(path))
    return list(dict.fromkeys(options))


def brands(query: str, category: str) -> list[str]:
    params = {'keyword': query[:100]}
    if category:
        wanted = [part.strip().casefold() for part in category.split('>') if part.strip()]
        rows = category_rows()
        found = None
        for part in wanted:
            found = next((row for row in rows if str(row.get('title') or '').strip().casefold() == part), None)
            if found is None:
                raise VintedConnectionError('Choose a category from Vinted’s current catalog before searching brands.')
            rows = found.get('catalogs') or found.get('children') or []
        if found and found.get('id') is not None:
            params['category_id'] = str(found['id'])
    rows = _read('/api/v2/item_upload/brands?' + urlencode(params), 'brands')
    return list(dict.fromkeys(row['title'] for row in rows if isinstance(row, dict)
                              and isinstance(row.get('title'), str) and row['title'].strip()))
