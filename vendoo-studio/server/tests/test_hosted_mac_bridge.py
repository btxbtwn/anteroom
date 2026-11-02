"""Exercise pywebview's actual API bootstrap under the hosted response policy."""
import html
import json
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vendoo_studio.hosted import create_app


@pytest.mark.skipif(sys.platform != 'darwin', reason='Requires macOS WebKit')
def test_hosted_policy_allows_native_api_bootstrap():
    import webview
    from AppKit import NSApplication
    from Foundation import NSDate, NSObject, NSRunLoop
    from WebKit import WKWebView, WKWebViewConfiguration

    policy = TestClient(create_app()).get('/api/health').headers['content-security-policy']
    source = (Path(webview.__file__).parent / 'js' / 'api.js').read_text()
    source = source % {'token': 'test', 'platform': 'cocoa'}
    functions = json.dumps([{'func': 'connect_chatgpt_worker', 'params': ['token', 'owner']}])
    script = source + '\nwindow.pywebview._createApi(' + functions + ');'
    script += """
    window.pywebview._jsApiCallback = (funcName, params) => {
        window.nativeCall = {funcName, params};
    };
    window.pywebview.api.connect_chatgpt_worker('test-session', 'test-owner');
    JSON.stringify({call: window.nativeCall, inlineRan: window.inlineRan === true})
    """
    result = []

    class Navigation(NSObject):
        def webView_didFinishNavigation_(self, view, _navigation):
            def complete(value, error):
                result.append((value, error))
            view.evaluateJavaScript_completionHandler_(script, complete)

    NSApplication.sharedApplication()
    delegate = Navigation.alloc().init()
    view = WKWebView.alloc().initWithFrame_configuration_(
        ((0, 0), (200, 200)), WKWebViewConfiguration.alloc().init(),
    )
    view.setNavigationDelegate_(delegate)
    view.loadHTMLString_baseURL_(
        '<meta http-equiv="Content-Security-Policy" content="' + html.escape(policy, quote=True)
        + '"><script>window.inlineRan = true;</script>',
        None,
    )
    deadline = time.monotonic() + 15
    while not result and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.1))
    view.setNavigationDelegate_(None)
    assert result, 'WebKit did not finish the bridge probe'
    value, error = result[0]
    assert error is None, str(error)
    assert json.loads(value) == {
        'call': {'funcName': 'connect_chatgpt_worker', 'params': ['test-session', 'test-owner']},
        'inlineRan': False,
    }
