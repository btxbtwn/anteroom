"""Anteroom Connector: a menu-bar subscription worker with a connection window."""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import threading
from pathlib import Path


def install_menu(window, api, quit_app):
    """AppKit runs on its main thread; the menu retains its action target."""
    appkit = importlib.import_module('AppKit')
    foundation = importlib.import_module('Foundation')

    class AnteroomConnectorMenuActions(foundation.NSObject):
        def connectorOpen_(self, _sender):
            window.show()

        def connectorPause_(self, _sender):
            paused = not api._worker.status()['paused']
            api._worker.set_paused(paused)
            self.pause_item.setTitle_('Resume' if paused else 'Pause')

        def connectorQuit_(self, _sender):
            quit_app()

        def menuWillOpen_(self, _menu):
            state = api._worker.status()
            self.pause_item.setTitle_('Resume' if state['paused'] else 'Pause')
            labels = {'connected': 'Connected to Anteroom', 'connecting': 'Connecting…', 'offline': 'Waiting for network',
                      'in_use': 'Another connector is connected', 'sign_in_required': 'Reconnect your account'}
            self.status_item.setTitle_('Paused' if state['paused'] else labels.get(state['connection'], 'Connect your account'))

    target = AnteroomConnectorMenuActions.alloc().init()
    menu = appkit.NSMenu.alloc().init()
    title = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Anteroom Connector', None, '')
    title.setEnabled_(False)
    target.status_item = title
    menu.addItem_(title)
    for label, selector in (('Connections…', 'connectorOpen:'), ('Pause', 'connectorPause:'), ('Quit Anteroom Connector', 'connectorQuit:')):
        item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, selector, 'q' if selector == 'connectorQuit:' else '')
        item.setTarget_(target)
        menu.addItem_(item)
        if selector == 'connectorPause:':
            target.pause_item = item
    menu.setDelegate_(target)
    status = appkit.NSStatusBar.systemStatusBar().statusItemWithLength_(appkit.NSVariableStatusItemLength)
    status.button().setTitle_('AC')
    status.button().setToolTip_('Anteroom Connector — subscriptions on this Mac')
    status.setMenu_(menu)
    return status, target


def run_connector():
    import webview
    AppHelper = importlib.import_module('PyObjCTools.AppHelper')
    from vendoo_studio.config import HOSTED_URL, user_data_root
    from vendoo_studio.services.connector import ConnectorAPI

    window = None
    closing = threading.Event()
    native = []
    api = ConnectorAPI(lambda: window)
    window = webview.create_window('Anteroom Connector', HOSTED_URL + '/?connector', js_api=api,
                                   width=660, height=760, min_size=(480, 540))
    if window is None:
        api._shutdown()
        raise RuntimeError('Could not open the connector connection window.')

    def quit_app():
        if closing.is_set():
            return
        closing.set()
        window.hide()

        def finish():
            api._shutdown()
            window.destroy()
        threading.Thread(target=finish, daemon=True, name='connector-quit').start()

    def hide_on_close():
        if closing.is_set():
            return True
        window.hide()
        return False

    def ready():
        AppHelper.callAfter(lambda: native.extend(install_menu(window, api, quit_app)))

    window.events.closing += hide_on_close
    try:
        webview.start(ready, gui='cocoa', private_mode=False, storage_path=str(user_data_root() / 'webview'))
    finally:
        api._shutdown()


def smoke_probe():
    """Exercise the packaged native API without network, sign-in, or a seller's data."""
    from types import SimpleNamespace
    from vendoo_studio.config import HOSTED_URL
    from vendoo_studio.services.connector import ConnectorAPI
    # Import the runtime dependencies PyInstaller must include. No credential reads.
    import webview  # noqa: F401
    import claude_agent_sdk  # noqa: F401
    from vendoo_studio.providers import claude_agent, chatgpt_codex  # noqa: F401

    api = ConnectorAPI(lambda: SimpleNamespace(get_current_url=lambda: HOSTED_URL + '/?connector'))
    try:
        api.connector_pause(True)
        assert api._worker.status()['paused']
        api.connector_pause(False)
        assert not api._worker.status()['paused']
        assert 'vendoo_studio.database' not in sys.modules, 'Connector started the listing database'
        print(json.dumps({'ok': True, 'app': 'Anteroom Connector', 'database_started': False}))
    finally:
        api._shutdown()


def main():
    parser = argparse.ArgumentParser(description='Connect desktop subscriptions to Anteroom web and iPhone.')
    parser.add_argument('--smoke', action='store_true', help='Verify the package offline on throwaway data, then exit.')
    args = parser.parse_args()
    if args.smoke:
        with tempfile.TemporaryDirectory(prefix='anteroom-connector-smoke-') as directory:
            os.environ['VENDOO_STUDIO_DATA_DIR'] = directory
            smoke_probe()
        return
    if sys.platform != 'darwin':
        raise SystemExit('Anteroom Connector currently requires macOS 13 or later.')
    os.environ.setdefault('VENDOO_STUDIO_DATA_DIR', str(Path.home() / 'Library' / 'Application Support' / 'Anteroom Connector'))
    run_connector()


if __name__ == '__main__':
    main()
