"""Code the dead-code check cannot see being called. Say who calls each one."""
# ruff: noqa

from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler
from logging import Handler

# HTMLParser calls these by name while it parses.
HTMLParser.handle_starttag
HTMLParser.handle_endtag
HTMLParser.handle_data
# BaseHTTPRequestHandler calls these by name for each request.
BaseHTTPRequestHandler.do_GET
BaseHTTPRequestHandler.log_message
# logging calls this when a handler fails.
Handler.handleError
# keyring backends: keyring calls this.
delete_password

# PyJWT calls the local sandbox key provider through its virtual fetch_data method.
from jwt import PyJWKClient
PyJWKClient.fetch_data

# direct_preflight.requirement("vinted") registers this callback for preflight checks.
vinted_requirements
