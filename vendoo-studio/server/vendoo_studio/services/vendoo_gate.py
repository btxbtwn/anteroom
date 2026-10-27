"""Refuse Vendoo calls while Settings has Vendoo turned off."""

VENDOO_OFF_DETAIL = (
    "Vendoo is off. Anteroom lists on the marketplace directly and does not call Vendoo."
)

_JOB_SUFFIXES = ("/open", "/import-draft", "/vendoo-item", "/resolve-category")


def vendoo_request_blocked(method: str, path: str) -> bool:
    clean = path.split("?", 1)[0].rstrip("/") or "/"
    if "/vendoo-api" in clean or "/imports/vendoo" in clean:
        return True
    if "/jobs/" in clean and "/browser" in clean:
        return True
    if clean.endswith("/jobs/send") or clean.endswith("/jobs/send-preview") or clean.endswith("/ensure-draft"):
        return True
    if "/jobs/" in clean and clean.endswith(_JOB_SUFFIXES):
        return True
    if method.upper() == "POST" and clean.endswith("/catalog/sync"):
        return True
    if clean.endswith("/vendoo-link") or clean.endswith("/vendoo-link-duplicates"):
        return True
    return False
