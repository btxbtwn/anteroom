import truststore
from pillow_heif import register_heif_opener

# The packaged app's Python has no CA bundle of its own, so stdlib HTTPS (the
# box-scout crawl, for one) failed certificate checks. Verify against the
# system trust store (the macOS Keychain) instead. This has to run before the
# first urlopen: urllib builds its shared opener, HTTPS context included, on
# that first call and keeps it, and the desktop launcher's health check makes
# that call before the server is imported.
truststore.inject_into_ssl()

# Photos dragged from Finder or the Files app arrive as the iPhone's HEIC.
# Pillow cannot open those by itself, so thumbnails fell back to the original
# and the vision encoder sent HEIC bytes labelled as JPEG, which no model reads.
register_heif_opener()
