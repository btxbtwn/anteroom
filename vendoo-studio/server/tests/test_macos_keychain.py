from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from vendoo_studio.services.macos_keychain import set_password


@pytest.fixture
def security():
    api = SimpleNamespace(
        SecItemUpdate=Mock(return_value=0),
        SecItemAdd=Mock(return_value=(0, None)),
        errSecItemNotFound=-25300,
        errSecSuccess=0,
        kSecAttrAccount="account", kSecAttrService="service",
        kSecClass="class", kSecClassGenericPassword="generic", kSecValueData="data",
    )
    with patch.dict(sys.modules, {"Security": api}):
        yield api


def test_updates_existing_item_without_recreating_its_permissions(security):
    set_password("vendoo-studio", "studio-secrets", "café")
    security.SecItemUpdate.assert_called_once_with(
        {"class": "generic", "service": "vendoo-studio", "account": "studio-secrets"},
        {"data": "café".encode()},
    )
    security.SecItemAdd.assert_not_called()


def test_creates_only_when_item_is_absent(security):
    security.SecItemUpdate.return_value = security.errSecItemNotFound
    set_password("vendoo-studio", "studio-secrets", "secret")
    security.SecItemAdd.assert_called_once_with({
        "class": "generic", "service": "vendoo-studio", "account": "studio-secrets", "data": b"secret",
    }, None)


@pytest.mark.parametrize("status", [-128, -25293, -25308])
def test_denied_update_never_replaces_the_item(security, status):
    security.SecItemUpdate.return_value = status
    with pytest.raises(RuntimeError, match=str(status)):
        set_password("vendoo-studio", "studio-secrets", "secret")
    security.SecItemAdd.assert_not_called()


def test_failed_creation_is_reported_without_exposing_secret(security):
    security.SecItemUpdate.return_value = security.errSecItemNotFound
    security.SecItemAdd.return_value = (-25299, None)
    with pytest.raises(RuntimeError, match=r"^macOS Keychain write failed \(-25299\)$"):
        set_password("vendoo-studio", "studio-secrets", "secret")
