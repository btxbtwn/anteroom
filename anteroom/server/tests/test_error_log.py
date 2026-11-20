import logging
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from anteroom.main import app
from anteroom.services import error_log


class ErrorLogTest(unittest.TestCase):
    def setUp(self):
        error_log.install()
        error_log.clear()

    def tearDown(self):
        error_log.clear()

    def test_keeps_warnings_and_errors_newest_first(self):
        log = logging.getLogger("anteroom.listing_generate")
        log.info("not kept")
        log.warning("photo analysis failed: %s", "Invalid User API Key")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("listing generation crashed")

        entries = error_log.list_entries()
        self.assertEqual([e["message"] for e in entries], [
            "listing generation crashed",
            "photo analysis failed: Invalid User API Key",
        ])
        self.assertEqual(entries[0]["level"], "error")
        self.assertEqual(entries[0]["source"], "listing_generate")
        self.assertIn("RuntimeError: boom", entries[0]["detail"])
        self.assertIsNone(entries[1]["detail"])

    def test_masks_secrets(self):
        logging.getLogger("anteroom.cursor").warning("request failed with api_key=cursor_supersecret123")
        message = error_log.list_entries()[0]["message"]
        self.assertNotIn("supersecret", message)

    def test_install_is_idempotent(self):
        error_log.install()
        logging.getLogger("anteroom").warning("once")
        self.assertEqual(len(error_log.list_entries()), 1)

    def test_terminal_and_parent_handlers_never_receive_secrets(self):
        for name in error_log.LOGGERS:
            with self.subTest(name=name):
                output = io.StringIO()
                parent_output = io.StringIO()
                logger = logging.getLogger(name)
                parent = logger.parent
                with patch.object(logger, "handlers", [logging.StreamHandler(output)]), patch.object(
                    parent, "handlers", [logging.StreamHandler(parent_output)]
                ):
                    error_log.install()
                    try:
                        raise RuntimeError("api_key=terminal_secret_123")
                    except RuntimeError:
                        logger.exception("request failed token=%s", "another_secret_456")
                self.assertIn("RuntimeError", output.getvalue())
                self.assertNotIn("terminal_secret_123", output.getvalue())
                self.assertNotIn("another_secret_456", output.getvalue())
                self.assertEqual(parent_output.getvalue(), "")
                self.assertNotIn("terminal_secret_123", str(error_log.list_entries()))
                self.assertNotIn("another_secret_456", str(error_log.query_entries()))

    def test_settings_routes_list_and_clear(self):
        logging.getLogger("anteroom").warning("shown in settings")
        client = TestClient(app)
        entries = client.get("/api/settings/error-logs").json()["entries"]
        self.assertEqual(entries[0]["message"], "shown in settings")
        self.assertEqual(client.delete("/api/settings/error-logs").json(), {"ok": True})
        self.assertEqual(client.get("/api/settings/error-logs").json(), {"entries": []})

    def test_failed_console_write_does_not_dump_the_raw_record(self):
        from unittest.mock import Mock

        stream = Mock()
        stream.write.side_effect = OSError("console unavailable")
        logger = logging.getLogger("anteroom")
        fallback = io.StringIO()
        with patch.object(logger, "handlers", [logging.StreamHandler(stream)]), patch("sys.stderr", fallback):
            error_log.install()
            logger.warning("api_key=%s", "failed_write_secret_123")
        self.assertNotIn("failed_write_secret_123", fallback.getvalue())


def test_hosted_assistant_timings_keep_individual_samples(tmp_path, monkeypatch):
    from anteroom.services import error_alerts

    path = tmp_path / 'timings.jsonl'
    monkeypatch.setattr(error_log, '_disk_path', lambda: path)
    # Force the existing ring's compaction path without generating thousands of logs.
    monkeypatch.setattr(error_log, '_DISK_MAX', 2)
    path.write_text('{"message":"old"}\n' * 40000)
    logger = logging.getLogger('anteroom.assistant.timing')
    reports = []

    class Alerts(logging.Handler):
        def emit(self, record):
            reports.append(error_alerts.build_report(record))

    alert = Alerts(level=logging.WARNING)
    parent = logging.getLogger('anteroom.assistant')
    with patch.object(parent, 'handlers', [alert]), patch.object(parent, 'propagate', False), patch.object(
        logger, 'handlers', []
    ), patch.object(logger, 'propagate', True), patch.object(logger, 'level', logging.WARNING):
        error_log.install_assistant_timings()
        error_log.install_assistant_timings()
        assert len(logger.handlers) == 1
        assert parent.handlers == [alert]
        for total in (80000, 40000):
            logger.warning('assistant answer timing: outcome=answered total_ms=%s', total)
    entries = error_log.query_entries(grep='assistant answer timing')
    assert [entry['message'].split('total_ms=')[1] for entry in entries] == ['40000', '80000']
    assert path.stat().st_size < 512_000
    assert len(path.read_text().splitlines()) <= 3
    # Both samples still reach alert handling, even though its grouping merges them.
    assert len(reports) == 2
    assert reports[0]['fingerprint'] == reports[1]['fingerprint']
    assert reports[0]['message'] != reports[1]['message']


def test_timing_handler_preserves_assistant_stderr_fallback(monkeypatch):
    assistant_logger = logging.getLogger('anteroom.assistant')
    timing_logger = logging.getLogger('anteroom.assistant.timing')
    captured = []

    class Fallback(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    with patch.object(assistant_logger, 'handlers', []), patch.object(
        assistant_logger, 'propagate', False
    ), patch.object(assistant_logger, 'level', logging.ERROR), patch.object(
        timing_logger, 'handlers', []
    ), patch.object(logging, 'lastResort', Fallback()):
        error_log.install_assistant_timings()
        assistant_logger.error('controlled preparation failure')
    assert captured == ['controlled preparation failure']


def test_dsn_redacted_before_message_or_detail_truncation(monkeypatch):
    from anteroom.services import error_log
    import logging

    monkeypatch.setattr(error_log, 'MAX_MESSAGE_CHARS', 32)
    monkeypatch.setattr(error_log, 'MAX_DETAIL_CHARS', 32)
    entries = []
    monkeypatch.setattr(error_log, '_append_disk', entries.append)
    message = 'postgres://alice:' + 'sensitive' * 10 + '@db/app'
    record = logging.LogRecord('anteroom.test', logging.ERROR, __file__, 1, message, (), None)
    error_log.AppLogHandler().emit(record)
    assert 'sensitive' not in entries[-1]['message']
    try:
        raise ValueError(message)
    except ValueError:
        import sys
        record.exc_info = sys.exc_info()
    handler = error_log.ErrorLogHandler()
    handler.emit(record)
    assert 'sensitive' not in str(error_log.list_entries())
