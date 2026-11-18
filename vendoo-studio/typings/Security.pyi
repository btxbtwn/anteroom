from collections.abc import Mapping

errSecItemNotFound: int
errSecSuccess: int
kSecAttrAccount: str
kSecAttrService: str
kSecClass: str
kSecClassGenericPassword: str
kSecValueData: str

def SecItemUpdate(query: Mapping[str, object], attributes: Mapping[str, object]) -> int: ...
def SecItemAdd(attributes: Mapping[str, object], result: None) -> tuple[int, object | None]: ...
