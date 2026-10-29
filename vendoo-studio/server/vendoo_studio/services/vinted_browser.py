"""Vinted catalogs read in Anteroom's private account window."""
from __future__ import annotations

import json
from urllib.parse import urlencode

from vendoo_studio.services.account_browser import AccountConnectionError as VintedConnectionError, PrivateAccountWindow

_session = PrivateAccountWindow('Vinted', 'https://www.vinted.com', '/items/new')
install = _session.install
connect = _session.connect
shutdown = _session.shutdown


def _read(path: str, result_key: str):
    if (path, result_key) != ('/api/v2/item_upload/catalogs', 'catalogs') and not (
        path.startswith('/api/v2/item_upload/brands?') and result_key == 'brands'
    ):
        raise ValueError('This connection only reads Vinted categories and brands.')
    headers = r"""(() => {
      const headers = {Accept:'application/json', Locale:'en-US', 'x-enable-multiple-size-groups':'true'};
      const csrf = document.documentElement.innerHTML.match(/\\?"CSRF_TOKEN\\?"\s*:\s*\\?"([^"\\]+)\\?"/);
      if (csrf && csrf[1].length <= 200 && !/\s/.test(csrf[1])) headers['x-csrf-token'] = csrf[1];
      const anon = document.cookie.match(/(?:^|;\s*)anon_id=([^;]+)/);
      if (anon) headers['x-anon-id'] = decodeURIComponent(anon[1]);
      return headers;
    })()"""
    return _session.read(path, 'body[' + json.dumps(result_key) + ']', headers=headers)


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
