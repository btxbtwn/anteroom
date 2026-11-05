"""Settings → Connections on the web: Chrome's marketplace sign-ins, read through the seller's own Anteroom tab."""
from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_hosted_browser_relay import _answer, _in_background
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import USER_ID, jwks, signing_keys  # noqa: F401
from vendoo_studio.services import hosted_browser_relay

CAPABLE = 'marketplace.actions,marketplace.sessions'

