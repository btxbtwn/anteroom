from __future__ import annotations


def set_password(service: str, account: str, value: str) -> None:
    """Update the existing item so its access permissions survive token rotation.

    keyring's macOS setter deletes and recreates the item. Use Apple's update
    operation instead: a failed save must leave the previous credentials intact.
    """
    from Security import (
        SecItemAdd,
        SecItemUpdate,
        errSecItemNotFound,
        errSecSuccess,
        kSecAttrAccount,
        kSecAttrService,
        kSecClass,
        kSecClassGenericPassword,
        kSecValueData,
    )

    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecAttrAccount: account,
    }
    data = {kSecValueData: value.encode("utf-8")}
    status = SecItemUpdate(query, data)
    if status == errSecItemNotFound:
        status, _ = SecItemAdd({**query, **data}, None)
    if status != errSecSuccess:
        raise RuntimeError(f"macOS Keychain write failed ({status})")
