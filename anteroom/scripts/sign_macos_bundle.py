#!/usr/bin/env python3
"""Sign a packaged Mac app inside-out for Developer ID distribution.

    sign_macos_bundle.py <path/to/App.app> <signing identity>

Apple discourages ``codesign --deep`` for signing: it signs nested code without
its own entitlements or options and in no guaranteed order. This signs every
Mach-O file first (deepest first), then each nested bundle, then the app, all
with the hardened runtime and a secure timestamp, which notarization requires.

Entitlements are the fewest the frozen Python runtime needs:

- The app itself needs none when its certificate carries an Apple Team ID: all
  code inside is re-signed here with that same Team ID, so library validation
  passes. A certificate without a Team ID (the throwaway one pull requests use,
  the older self-signed release certificate, ad-hoc local builds) cannot pass
  library validation, so those builds disable it.
- Helper executables the app starts (the Node runtime inside cursor_sdk) run
  V8, which needs ``allow-jit`` under the hardened runtime. Their vendor
  entitlements are replaced, not kept: Node's own set includes
  ``get-task-allow``, which notarization refuses.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

DISABLE_LIBRARY_VALIDATION = "com.apple.security.cs.disable-library-validation"
ALLOW_JIT = "com.apple.security.cs.allow-jit"

# Mach-O file types (mach-o/loader.h).
MH_EXECUTE = 2
# Loadable code: dylibs and bundles (Python extension modules). Object files,
# debug symbols and the like are not run and cannot be signed.
LOADABLE = {6, 8}
THIN_MAGICS = {
    b"\xfe\xed\xfa\xce": ">", b"\xfe\xed\xfa\xcf": ">",
    b"\xce\xfa\xed\xfe": "<", b"\xcf\xfa\xed\xfe": "<",
}
FAT_MAGICS = {b"\xca\xfe\xba\xbe": 20, b"\xca\xfe\xba\xbf": 32}
# Nested directories codesign treats as bundles, when they hold an Info.plist.
BUNDLE_SUFFIXES = (".framework", ".app", ".appex", ".xpc", ".bundle", ".plugin")
CODESIGN_BATCH = 200


def macho_filetype(path: Path) -> int | None:
    """The Mach-O file type of ``path``, or None when it is not Mach-O."""
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
            magic = header[:4]
            if magic in THIN_MAGICS and len(header) >= 16:
                return struct.unpack(THIN_MAGICS[magic] + "I", header[12:16])[0]
            if magic in FAT_MAGICS and len(header) >= 8:
                count = struct.unpack(">I", header[4:8])[0]
                # Java class files share 0xcafebabe; their next word is a
                # class-file version far above any real architecture count.
                if not 0 < count < 32:
                    return None
                arch_size = FAT_MAGICS[magic]
                offset_field = slice(8 + 8, 8 + 8 + (8 if arch_size == 32 else 4))
                raw = header[offset_field] if len(header) >= offset_field.stop else b""
                if not raw:
                    return None
                offset = int.from_bytes(raw, "big")
                handle.seek(offset)
                thin = handle.read(16)
                if thin[:4] in THIN_MAGICS and len(thin) == 16:
                    return struct.unpack(THIN_MAGICS[thin[:4]] + "I", thin[12:16])[0]
    except OSError:
        return None
    return None


def _depth(path: Path) -> int:
    return len(path.parts)


def _is_nested_bundle(path: Path) -> bool:
    if not path.name.endswith(BUNDLE_SUFFIXES):
        return False
    return any((path / plist).is_file() for plist in (
        "Contents/Info.plist", "Resources/Info.plist", "Versions/Current/Resources/Info.plist",
    ))


def signing_plan(app: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """(libraries, helper executables, nested bundles) to sign before ``app``.

    Symbolic links are skipped: PyInstaller links Resources and Frameworks to
    each other, and codesign signs a file once, at its real path. Each list is
    ordered deepest first so nested code is signed before what contains it.
    """
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    main_executable = app / "Contents" / "MacOS" / str(info.get("CFBundleExecutable") or app.stem)
    libraries: list[Path] = []
    executables: list[Path] = []
    bundles: list[Path] = []
    for root, dirs, files in os.walk(app):
        base = Path(root)
        for name in dirs:
            candidate = base / name
            if not candidate.is_symlink() and _is_nested_bundle(candidate):
                bundles.append(candidate)
        for name in files:
            candidate = base / name
            if candidate.is_symlink() or candidate == main_executable:
                continue
            kind = macho_filetype(candidate)
            if kind == MH_EXECUTE:
                executables.append(candidate)
            elif kind in LOADABLE:
                libraries.append(candidate)
    order = lambda items: sorted(items, key=lambda item: (-_depth(item), str(item)))  # noqa: E731
    return order(libraries), order(executables), order(bundles)


def app_entitlements(team_id: str | None) -> dict[str, bool]:
    return {} if team_id else {DISABLE_LIBRARY_VALIDATION: True}


def helper_entitlements(team_id: str | None) -> dict[str, bool]:
    return {ALLOW_JIT: True, **app_entitlements(team_id)}


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def team_id_for(identity: str, work: Path) -> str | None:
    """The Apple Team ID ``identity`` signs with, found by signing a probe."""
    probe = work / "team-probe"
    shutil.copyfile("/usr/bin/true", probe)
    probe.chmod(0o755)
    signed = _run(["codesign", "--force", "--sign", identity, str(probe)])
    if signed.returncode != 0:
        raise SystemExit(f"codesign cannot sign with {identity!r}: {signed.stderr.strip()}")
    detail = _run(["codesign", "-dv", str(probe)])
    for line in (detail.stderr + detail.stdout).splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.split("=", 1)[1].strip()
            return None if value in {"", "not set"} else value
    return None


def codesign_command(identity: str, entitlements: Path | None, *, preserve_entitlements: bool = False) -> list[str]:
    command = ["codesign", "--force", "--options", "runtime", "--sign", identity]
    # Ad-hoc signatures cannot carry a timestamp.
    command.append("--timestamp" if identity != "-" else "--timestamp=none")
    if entitlements is not None:
        command += ["--entitlements", str(entitlements)]
    elif preserve_entitlements:
        command.append("--preserve-metadata=entitlements")
    return command


def _sign(command: list[str], paths: list[Path]) -> None:
    for start in range(0, len(paths), CODESIGN_BATCH):
        batch = paths[start:start + CODESIGN_BATCH]
        result = _run([*command, *map(str, batch)])
        if result.returncode != 0:
            raise SystemExit(f"codesign failed:\n{result.stderr.strip()}")


def _write_entitlements(path: Path, values: dict[str, bool]) -> Path | None:
    if not values:
        return None
    path.write_bytes(plistlib.dumps(values))
    return path


def sign_app(app: Path, identity: str) -> None:
    if not (app / "Contents" / "Info.plist").is_file():
        raise SystemExit(f"{app} is not an app bundle.")
    with tempfile.TemporaryDirectory(prefix="anteroom-sign-") as tmp:
        work = Path(tmp)
        team_id = team_id_for(identity, work)
        libraries, executables, bundles = signing_plan(app)
        app_plist = _write_entitlements(work / "app.plist", app_entitlements(team_id))
        helper_plist = _write_entitlements(work / "helper.plist", helper_entitlements(team_id))
        print(f"Signing {app.name} with {identity} (Team ID {team_id or 'none'}): "
              f"{len(libraries)} libraries, {len(executables)} helper executables, "
              f"{len(bundles)} nested bundles")
        for path in executables:
            print(f"  helper executable (allow-jit): {path.relative_to(app)}")
        _sign(codesign_command(identity, None), libraries)
        _sign(codesign_command(identity, helper_plist), executables)
        # A bundle's main executable was signed above with its entitlements;
        # signing the bundle seals its resources and keeps them.
        for bundle in bundles:
            _sign(codesign_command(identity, None, preserve_entitlements=True), [bundle])
        _sign(codesign_command(identity, app_plist), [app])
    verify = _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    if verify.returncode != 0:
        raise SystemExit(f"Signed app failed verification:\n{verify.stderr.strip()}")
    print(verify.stderr.strip())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app", type=Path)
    parser.add_argument("identity")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        raise SystemExit("Signing needs macOS codesign.")
    sign_app(args.app.resolve(), args.identity)


if __name__ == "__main__":
    main()
