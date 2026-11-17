"""The local stubs reject bad calls and match installed Apple bridge metadata."""
from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_invalid_saved_url_is_rejected_before_loading_webview(monkeypatch):
    from vendoo_studio import desktop

    requests = []
    monkeypatch.setattr(desktop, 'saved_workspace_url', lambda: 'invalid URL')
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSURL=SimpleNamespace(URLWithString_=lambda *_: None),
        NSURLRequest=SimpleNamespace(requestWithURL_=lambda *_: requests.append(True))))
    with pytest.raises(ValueError, match='workspace URL is invalid'):
        desktop._load_app_url(SimpleNamespace(loadRequest_=lambda *_: requests.append(True)))
    assert requests == []


@pytest.mark.skipif(sys.platform != 'darwin', reason='installed macOS framework exports')
@pytest.mark.parametrize('module', ['Foundation', 'AppKit', 'Security', 'Vision', 'Quartz', 'objc'])
def test_stub_public_exports_exist_at_runtime(module):
    runtime = importlib.import_module(module)
    tree = ast.parse((ROOT / 'typings' / f'{module}.pyi').read_text())
    for node in tree.body:
        name = node.name if isinstance(node, (ast.ClassDef, ast.FunctionDef)) else (
            node.target.id if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) else '')
        if name and not name.startswith('_'):
            assert hasattr(runtime, name), (module, name)


@pytest.mark.parametrize('missing', ['handler', 'request', 'address'])
def test_missing_vision_results_are_reported_and_buffers_unlocked(monkeypatch, missing):
    from PIL import Image

    from vendoo_studio.services import photo_edits

    unlocked = []
    observation = SimpleNamespace(allInstances=lambda: object(),
        generateScaledMaskForImageForInstances_fromRequestHandler_error_=lambda *_: (object(), None))
    handler = SimpleNamespace(performRequests_error_=lambda *_: (True, None))
    request = SimpleNamespace(results=lambda: [observation])
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSData=SimpleNamespace(dataWithBytes_length_=lambda *_: object())))
    monkeypatch.setitem(sys.modules, 'Vision', SimpleNamespace(
        VNImageRequestHandler=SimpleNamespace(alloc=lambda: SimpleNamespace(
            initWithData_options_=lambda *_: None if missing == 'handler' else handler)),
        VNGenerateForegroundInstanceMaskRequest=SimpleNamespace(alloc=lambda: SimpleNamespace(
            init=lambda: None if missing == 'request' else request))))
    monkeypatch.setitem(sys.modules, 'Quartz', SimpleNamespace(
        CVPixelBufferLockBaseAddress=lambda *_: 0, kCVPixelBufferLock_ReadOnly=1,
        CVPixelBufferGetWidth=lambda *_: 2, CVPixelBufferGetHeight=lambda *_: 2,
        CVPixelBufferGetBytesPerRow=lambda *_: 8, CVPixelBufferGetBaseAddress=lambda *_: None,
        CVPixelBufferUnlockBaseAddress=lambda *_: unlocked.append(True)))
    with pytest.raises(photo_edits.BackgroundRemovalError, match='initialize|empty pixel buffer'):
        photo_edits._vision_mask(Image.new('RGB', (2, 2)))
    assert unlocked == ([True] if missing == 'address' else [])


@pytest.mark.parametrize('invalid', [False, True])
def test_framework_type_contract(tmp_path, invalid):
    source = tmp_path / 'calls.py'
    source.write_text('''from Foundation import NSURL, NSURLRequest
from Security import SecItemAdd, SecItemUpdate
from Quartz import CVPixelBufferGetWidth
url = NSURL.URLWithString_("https://example.com")
if url is not None:
    request = NSURLRequest.requestWithURL_(url)
status: int = SecItemUpdate({"service": "synthetic"}, {"value": b"test"})
code, result = SecItemAdd({"service": "synthetic"}, None)
'''+('''SecItemUpdate([], {})
NSURL.URLWithString_(123)
CVPixelBufferGetWidth("not a pixel buffer")
NSURLRequest.requestWithURL_(None)
''' if invalid else ''))
    config = tmp_path / 'pyrightconfig.json'
    config.write_text(json.dumps({'include': [str(source)], 'stubPath': str(ROOT / 'typings'),
                                 'pythonVersion': '3.12', 'typeCheckingMode': 'standard'}))
    run = subprocess.run([sys.executable, '-m', 'basedpyright', '--project', str(config), '--outputjson'],
                         capture_output=True, text=True, timeout=60)
    result = json.loads(run.stdout)
    assert result['summary']['errorCount'] == (4 if invalid else 0), result
    assert run.returncode == (1 if invalid else 0), run.stderr


@pytest.mark.skipif(sys.platform != 'darwin', reason='installed macOS framework metadata')
def test_bridge_out_parameters_and_pixel_memory():
    import Quartz
    import Security
    import Vision

    assert Security.SecItemUpdate.__metadata__()['retval']['type'] == b'i'
    assert Security.SecItemAdd.__metadata__()['arguments'][1]['type'] == b'o^@'
    assert Vision.VNImageRequestHandler.performRequests_error_.__metadata__()['arguments'][3]['type'] == b'o^@'
    assert Quartz.CVPixelBufferGetBaseAddress.__metadata__()['retval']['null_accepted']
    # Only a disposable in-memory buffer; never an app, photo, Keychain or provider call.
    status, buffer = Quartz.CVPixelBufferCreate(None, 2, 2, Quartz.kCVPixelFormatType_OneComponent32Float, None, None)
    assert status == 0 and buffer is not None
    assert Quartz.CVPixelBufferLockBaseAddress(buffer, Quartz.kCVPixelBufferLock_ReadOnly) == 0
    try:
        address = Quartz.CVPixelBufferGetBaseAddress(buffer)
        assert address is not None
        raw = address.as_buffer(Quartz.CVPixelBufferGetBytesPerRow(buffer) * 2)
        assert isinstance(raw, memoryview)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(buffer, Quartz.kCVPixelBufferLock_ReadOnly)
