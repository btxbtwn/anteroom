"""The Mac app's inside-out signing plan and entitlements, without codesign."""

from __future__ import annotations

import importlib.util
import plistlib
import struct
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "sign_macos_bundle.py"
SPEC = importlib.util.spec_from_file_location("sign_macos_bundle", SCRIPT)
assert SPEC and SPEC.loader
sign_macos_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sign_macos_bundle)


def thin_macho(filetype: int) -> bytes:
    # 64-bit little-endian header: magic, cputype (arm64), subtype, filetype.
    return struct.pack("<IiiI", 0xFEEDFACF, 0x0100000C, 0, filetype) + b"\0" * 16


def fat_macho(filetype: int) -> bytes:
    thin = thin_macho(filetype)
    header = struct.pack(">II", 0xCAFEBABE, 1) + struct.pack(">iiIII", 0x0100000C, 0, 4096, len(thin), 14)
    return header.ljust(4096, b"\0") + thin


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_app(root: Path) -> Path:
    app = root / "Anteroom.app"
    write(app / "Contents" / "Info.plist", plistlib.dumps({"CFBundleExecutable": "Anteroom"}))
    write(app / "Contents" / "MacOS" / "Anteroom", thin_macho(2))
    framework = app / "Contents" / "Frameworks" / "Python.framework"
    write(framework / "Versions" / "3.12" / "Python", thin_macho(6))
    write(framework / "Versions" / "3.12" / "Resources" / "Info.plist", b"plist")
    (framework / "Versions" / "Current").symlink_to("3.12")
    (framework / "Resources").symlink_to("Versions/Current/Resources")
    (framework / "Python").symlink_to("Versions/Current/Python")
    write(app / "Contents" / "Frameworks" / "numpy" / "core" / "_multiarray.cpython-312-darwin.so", fat_macho(8))
    write(app / "Contents" / "Frameworks" / "libssl.3.dylib", thin_macho(6))
    write(app / "Contents" / "Resources" / "cursor_sdk" / "_vendor" / "bridge" / "bin" / "node", thin_macho(2))
    write(app / "Contents" / "Resources" / "cursor_sdk" / "_vendor" / "bridge" / "bin" / "cursor-sdk-bridge", b"#!/bin/sh\n")
    write(app / "Contents" / "Resources" / "Java.class", struct.pack(">II", 0xCAFEBABE, 52))
    write(app / "Contents" / "Resources" / "notes.bundle" / "readme.txt", b"not a real bundle")
    (app / "Contents" / "Resources" / "libssl.3.dylib").symlink_to("../Frameworks/libssl.3.dylib")
    return app


def test_macho_filetype_reads_thin_and_universal_binaries_only(tmp_path: Path) -> None:
    assert sign_macos_bundle.macho_filetype(write(tmp_path / "exe", thin_macho(2))) == 2
    assert sign_macos_bundle.macho_filetype(write(tmp_path / "lib.so", fat_macho(8))) == 8
    assert sign_macos_bundle.macho_filetype(write(tmp_path / "A.class", struct.pack(">II", 0xCAFEBABE, 52))) is None
    assert sign_macos_bundle.macho_filetype(write(tmp_path / "script", b"#!/bin/sh\n")) is None
    assert sign_macos_bundle.macho_filetype(write(tmp_path / "empty", b"")) is None


def test_plan_signs_nested_code_before_what_contains_it(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    libraries, executables, bundles = sign_macos_bundle.signing_plan(app)
    rel = lambda paths: [str(path.relative_to(app)) for path in paths]  # noqa: E731

    assert rel(libraries) == [
        "Contents/Frameworks/Python.framework/Versions/3.12/Python",
        "Contents/Frameworks/numpy/core/_multiarray.cpython-312-darwin.so",
        "Contents/Frameworks/libssl.3.dylib",
    ]
    # The app's own executable is signed last, with the app; links are signed
    # at their real path once; a directory named .bundle without an
    # Info.plist is not a bundle.
    assert rel(executables) == ["Contents/Resources/cursor_sdk/_vendor/bridge/bin/node"]
    assert rel(bundles) == ["Contents/Frameworks/Python.framework"]


def test_entitlements_are_minimal() -> None:
    # Developer ID: every binary carries the app's Team ID, so library
    # validation passes and the app needs no entitlement at all.
    assert sign_macos_bundle.app_entitlements("JWN57LH9PU") == {}
    # Without a Team ID no library can pass library validation.
    assert sign_macos_bundle.app_entitlements(None) == {
        "com.apple.security.cs.disable-library-validation": True,
    }
    # Node's V8 needs to map JIT memory under the hardened runtime. Node's own
    # get-task-allow entitlement is not carried over: notarization refuses it.
    assert sign_macos_bundle.helper_entitlements("JWN57LH9PU") == {"com.apple.security.cs.allow-jit": True}
    assert "com.apple.security.get-task-allow" not in sign_macos_bundle.helper_entitlements(None)


def test_codesign_uses_hardened_runtime_and_secure_timestamp() -> None:
    command = sign_macos_bundle.codesign_command("ABCDEF", Path("/tmp/app.plist"))
    assert command[:4] == ["codesign", "--force", "--options", "runtime"]
    assert "--timestamp" in command
    assert "--deep" not in command
    assert command[-2:] == ["--entitlements", "/tmp/app.plist"]
    # Ad-hoc local builds cannot be timestamped.
    assert "--timestamp=none" in sign_macos_bundle.codesign_command("-", None)
    assert "--preserve-metadata=entitlements" in sign_macos_bundle.codesign_command(
        "ABCDEF", None, preserve_entitlements=True,
    )


def test_plan_leaves_object_files_unsigned(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    write(app / "Contents" / "Resources" / "lib" / "stub.o", thin_macho(1))
    libraries, executables, _ = sign_macos_bundle.signing_plan(app)
    assert all(path.suffix != ".o" for path in [*libraries, *executables])
