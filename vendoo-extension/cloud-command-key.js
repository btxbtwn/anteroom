/* Public keys (JWK, ECDSA P-256) whose signatures this Bridge accepts on web Save draft commands.
   The hosted API holds the private key (Fly secret ANTEROOM_COMMAND_SIGNING_KEY).
   vendoo-studio/scripts/command-signing-key.py writes this file. With no key, the
   Bridge does not offer Save draft to the web app. */
var CLOUD_COMMAND_KEYS = [];
