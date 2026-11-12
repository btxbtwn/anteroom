# -*- mode: python ; coding: utf-8 -*-
"""The connector contains the native bridge and providers, not the listing app."""
from pathlib import Path

from PyInstaller.building.api import COLLECT, EXE, PYZ
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.osx import BUNDLE
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

STUDIO = Path(SPECPATH).parent
VERSION = (STUDIO / 'VERSION').read_text().strip()

a = Analysis(
    [str(STUDIO / 'server/vendoo_studio/connector.py')],
    pathex=[str(STUDIO / 'server')],
    datas=[*collect_data_files('webview'), (str(STUDIO / 'VERSION'), '.')],
    hiddenimports=[
        'webview.platforms.cocoa', 'keyring.backends.macOS', 'Security', 'AppKit', 'Foundation', 'WebKit',
        'PyObjCTools.AppHelper', *collect_submodules('claude_agent_sdk'),
    ],
    excludes=[
        'tkinter', 'watchfiles', 'cursor_sdk', 'numpy', 'Vision', 'fastapi', 'sqlalchemy', 'alembic',
        'vendoo_studio.database', 'vendoo_studio.models', 'vendoo_studio.repositories', 'vendoo_studio.routes',
        'vendoo_studio.services.device_routes',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
# Signed afterwards by scripts/sign_macos_bundle.py (see package-connector.sh).
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Anteroom Connector', console=False)
coll = COLLECT(exe, a.binaries, a.datas, name='Anteroom Connector')
app = BUNDLE(
    coll, name='Anteroom Connector.app', bundle_identifier='com.myanteroom.connector',
    icon=str(STUDIO / 'desktop/AppIcon.icns'),
    info_plist={
        'CFBundleName': 'Anteroom Connector', 'CFBundleDisplayName': 'Anteroom Connector',
        'CFBundleShortVersionString': VERSION, 'CFBundleVersion': VERSION,
        'LSMinimumSystemVersion': '13.0', 'LSUIElement': True, 'NSHighResolutionCapable': True,
        'NSApplicationCategoryType': 'public.app-category.utilities',
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    },
)
