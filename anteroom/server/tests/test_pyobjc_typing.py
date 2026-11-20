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
    from anteroom import desktop

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
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, ast.FunctionDef):
                        assert hasattr(getattr(runtime, name), member.name), (module, name, member.name)


@pytest.mark.parametrize('missing', ['handler', 'request', 'address'])
def test_missing_vision_results_are_reported_and_buffers_unlocked(monkeypatch, missing):
    from PIL import Image

    from anteroom.services import photo_edits

    unlocked = []
    null_address = object()
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
        CVPixelBufferGetBytesPerRow=lambda *_: 8, CVPixelBufferGetBaseAddress=lambda *_: null_address,
        CVPixelBufferUnlockBaseAddress=lambda *_: unlocked.append(True)))
    monkeypatch.setitem(sys.modules, 'objc', SimpleNamespace(varlist=memoryview, NULL=null_address))
    with pytest.raises(photo_edits.BackgroundRemovalError, match='initialize|empty pixel buffer'):
        photo_edits._vision_mask(Image.new('RGB', (2, 2)))
    assert unlocked == ([True] if missing == 'address' else [])


@pytest.mark.parametrize('invalid', [False, True])
def test_framework_type_contract(tmp_path, invalid):
    source = tmp_path / 'calls.py'
    common = '''from Foundation import NSURL, NSURLRequest, NSData, NSStringFromSelector
from AppKit import NSImage, NSApplication
from Security import SecItemAdd, SecItemUpdate
from Quartz import CVPixelBufferRef, CVPixelBufferGetWidth, CVPixelBufferGetBaseAddress
from Vision import VNImageRequestHandler, VNGenerateForegroundInstanceMaskRequest
from objc import selector, classAddMethods, varlist
url = NSURL.URLWithString_("https://example.com")
if url is not None:
    request = NSURLRequest.requestWithURL_(url)
status: int = SecItemUpdate({"service": "synthetic"}, {"value": b"test"})
code, result = SecItemAdd({"service": "synthetic"}, None)
name: str | None = NSStringFromSelector("copy:")
image = NSImage.alloc().initWithContentsOfFile_("synthetic.png")
NSApplication.sharedApplication().setApplicationIconImage_(image)
def callback(owner: object, view: object) -> None:
    pass
classAddMethods(NSApplication, [selector(callback, selector=b"callback:", signature=b"v@:@")])
data = NSData.dataWithBytes_length_(b"x", 1)
handler = VNImageRequestHandler.alloc().initWithData_options_(data, None)
vision_request = VNGenerateForegroundInstanceMaskRequest.alloc().init()
if handler is not None and vision_request is not None:
    ok, error = handler.performRequests_error_([vision_request], None)
def pixels(buffer: CVPixelBufferRef) -> None:
    address = CVPixelBufferGetBaseAddress(buffer)
    if isinstance(address, varlist):
        raw: memoryview = address.as_buffer(4)
'''
    bad_calls = [
        ('SecItemUpdate([], {})', 'reportArgumentType'),
        ('NSURL.URLWithString_(123)', 'reportArgumentType'),
        ('CVPixelBufferGetWidth("not a pixel buffer")', 'reportArgumentType'),
        ('NSURLRequest.requestWithURL_(NSURL.URLWithString_("https://example.com"))', 'reportArgumentType'),
        ('bad_status: int = SecItemAdd({}, None)', 'reportAssignmentType'),
        ('bad_name: str = NSStringFromSelector(None)', 'reportAssignmentType'),
        ('NSImage.alloc().initWithContentsOfFile_(123)', 'reportArgumentType'),
        ('selector("not callable", selector=b"x:", signature=b"v@:@")', 'reportArgumentType'),
        ('classAddMethods(123, [])', 'reportArgumentType'),
        ('handler.performRequests_error_([], None)', 'reportOptionalMemberAccess'),
        ('def bad_pixels(buffer: CVPixelBufferRef) -> None:\n    CVPixelBufferGetBaseAddress(buffer).as_buffer(4)',
         'reportAttributeAccessIssue'),
    ] if invalid else []
    lines = common.splitlines()
    expected = []
    for call, rule in bad_calls:
        parts = call.splitlines()
        expected.append((len(lines) + len(parts) - 1, rule))
        lines.extend(parts)
    source.write_text('\n'.join(lines) + '\n')
    config = tmp_path / 'pyrightconfig.json'
    config.write_text(json.dumps({'include': [str(source)], 'stubPath': str(ROOT / 'typings'),
                                 'pythonVersion': '3.12', 'typeCheckingMode': 'standard'}))
    run = subprocess.run([sys.executable, '-m', 'basedpyright', '--project', str(config), '--outputjson'],
                         capture_output=True, text=True, timeout=120)
    result = json.loads(run.stdout)
    errors = [d for d in result['generalDiagnostics'] if d['severity'] == 'error']
    assert sorted((d['range']['start']['line'], d.get('rule')) for d in errors) == sorted(expected), result
    assert all(Path(d['file']) == source for d in errors), result
    assert run.returncode == (1 if invalid else 0), run.stderr


@pytest.mark.skipif(sys.platform != 'darwin', reason='installed macOS framework metadata')
def test_bridge_out_parameters_and_pixel_memory():
    from Foundation import NSStringFromSelector
    import Quartz
    import Security
    import Vision
    import objc

    assert NSStringFromSelector(None) is None
    assert NSStringFromSelector('copy:') == NSStringFromSelector(b'copy:') == 'copy:'
    assert Security.SecItemUpdate.__metadata__()['retval']['type'] == b'i'
    assert Security.SecItemAdd.__metadata__()['arguments'][1]['type'] == b'o^@'
    assert Vision.VNImageRequestHandler.performRequests_error_.__metadata__()['arguments'][3]['type'] == b'o^@'
    assert Quartz.CVPixelBufferGetBaseAddress.__metadata__()['retval']['null_accepted']
    # Only a disposable in-memory buffer; never an app, photo, Keychain or provider call.
    status, buffer = Quartz.CVPixelBufferCreate(None, 2, 2, Quartz.kCVPixelFormatType_OneComponent32Float, None, None)
    assert status == 0 and buffer is not None
    assert Quartz.CVPixelBufferGetBaseAddress(buffer) is objc.NULL
    assert Quartz.CVPixelBufferLockBaseAddress(buffer, Quartz.kCVPixelBufferLock_ReadOnly) == 0
    try:
        address = Quartz.CVPixelBufferGetBaseAddress(buffer)
        assert isinstance(address, objc.varlist)
        raw = address.as_buffer(Quartz.CVPixelBufferGetBytesPerRow(buffer) * 2)
        assert isinstance(raw, memoryview)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(buffer, Quartz.kCVPixelBufferLock_ReadOnly)
