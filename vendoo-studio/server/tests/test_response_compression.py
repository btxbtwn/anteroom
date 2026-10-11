from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from vendoo_studio.main import app


class ResponseCompressionTest(unittest.TestCase):
    """Large text answers travel compressed; a phone on Tailscale pays for every byte."""

    def setUp(self):
        self.client = TestClient(app)

    def test_a_large_json_answer_is_gzipped_for_a_browser_that_accepts_it(self):
        response = self.client.get("/api/changelog", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-encoding"], "gzip")
        self.assertLess(int(response.headers["content-length"]), len(response.content))

    def test_it_is_sent_as_is_when_the_browser_does_not_accept_gzip(self):
        response = self.client.get("/api/changelog", headers={"Accept-Encoding": "identity"})
        self.assertNotIn("content-encoding", response.headers)

    def test_a_small_answer_is_not_compressed(self):
        response = self.client.get("/api/health", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", response.headers)
