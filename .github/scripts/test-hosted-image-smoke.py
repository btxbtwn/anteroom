"""Deterministic smoke-script regression; all Docker/git/sleep calls are mocks."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'hosted-image-smoke.sh'
DOCKER = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['SMOKE_MOCK_ROOT'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
mode = os.environ['SMOKE_MOCK_MODE']
if args[0] == 'build' or '-i' in args:
    sys.stdin.read()
if args[:2] == ['image', 'inspect']:
    print('linux/amd64' if args[-1] == '{{.Os}}/{{.Architecture}}' else 'synthetic linux/amd64 user=10001')
if 'pg_isready' in args:
    state = root / 'probes'
    count = int(state.read_text()) + 1 if state.exists() else 1
    state.write_text(str(count))
    tcp = '-h' in args and args[args.index('-h') + 1] == '127.0.0.1'
    tcp = tcp and '-p' in args and args[args.index('-p') + 1] == '5432'
    # The Unix socket accepts on the temporary server, then rejects on shutdown.
    # TCP cannot accept until the final server (third probe).
    ready = count >= 3 if tcp else count == 1
    if mode == 'timeout' or mode == 'final-refusal' and count >= 4:
        ready = False
    sys.exit(0 if ready else 1)
if args[0] == 'run' and 'anteroom-image-smoke-init' in args and mode == 'migration-failure':
    sys.exit(42)
if args[0] == 'logs':
    print('synthetic log for ' + args[1])
'''


class SmokeReadinessTests(unittest.TestCase):
    def run_smoke(self, mode):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        binary = root / 'bin'
        binary.mkdir()
        commands = {
            'docker': DOCKER,
            'git': '#!/bin/bash\ncase "$1" in rev-parse) echo synthetic-source;; archive) :;; *) exit 90;; esac\n',
            'sleep': '#!/bin/bash\necho sleep >> "$SMOKE_MOCK_ROOT/sleeps"\n',
        }
        for name, source in commands.items():
            executable = binary / name
            executable.write_text(source)
            executable.chmod(0o755)
        result = subprocess.run(['bash', str(SCRIPT)], cwd=root,
                                # Bash startup hooks and exported functions must not bypass the mocks.
                                env={'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                     'SMOKE_MOCK_ROOT': str(root), 'SMOKE_MOCK_MODE': mode},
                                check=False, capture_output=True, text=True, timeout=15)
        calls = [json.loads(line) for line in (root / 'calls.jsonl').read_text().splitlines()]
        return root, result, calls

    def assert_cleanup(self, root, calls):
        removal = next(i for i, call in enumerate(calls) if call[:2] == ['rm', '-f'])
        for container, filename in [('pg', 'postgres.log'), ('init', 'init.log'), ('app', 'server.log')]:
            name = 'anteroom-image-smoke-' + container
            self.assertLess(calls.index(['logs', name]), removal)
            self.assertIn(name, (root / 'image-validation' / filename).read_text())
            self.assertIn(name, calls[removal])
        self.assertIn(['network', 'rm', 'anteroom-image-smoke'], calls)

    def test_inherited_bash_hooks_cannot_bypass_mocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            startup = Path(temporary) / 'bash-env'
            startup.write_text('echo startup >> "$SMOKE_MOCK_ROOT/inherited-hook"\n')
            hooks = {
                'BASH_ENV': str(startup),
                'BASH_FUNC_docker%%': '() { echo function >> "$SMOKE_MOCK_ROOT/inherited-hook"; return 97; }',
            }
            for name, value in hooks.items():
                with self.subTest(hook=name), patch.dict(os.environ, {name: value}):
                    root, result, calls = self.run_smoke('ready')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((root / 'inherited-hook').exists())
                    self.assert_cleanup(root, calls)

    def test_socket_temporary_server_does_not_end_tcp_polling(self):
        root, result, calls = self.run_smoke('ready')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        probes = [call for call in calls if 'pg_isready' in call]
        self.assertEqual(len(probes), 4)  # Two refusals, final listener, final check.
        expected = ['exec', 'anteroom-image-smoke-pg', 'pg_isready', '-h', '127.0.0.1', '-p', '5432', '-U', 'postgres', '-d', 'smoke']
        self.assertTrue(all(call == expected for call in probes))
        migration = next(i for i, call in enumerate(calls) if call[0] == 'run' and 'anteroom-image-smoke-init' in call)
        self.assertGreater(migration, max(i for i, call in enumerate(calls) if 'pg_isready' in call))
        self.assertEqual((root / 'sleeps').read_text().splitlines(), ['sleep', 'sleep'])
        self.assert_cleanup(root, calls)

    def test_timeout_is_bounded_and_never_starts_migration(self):
        root, result, calls = self.run_smoke('timeout')
        self.assertEqual(result.returncode, 1)
        self.assertIn('final TCP readiness check', result.stderr)
        self.assertEqual(len([call for call in calls if 'pg_isready' in call]), 61)
        self.assertEqual(len((root / 'sleeps').read_text().splitlines()), 60)
        self.assertFalse(any(call[0] == 'run' and 'anteroom-image-smoke-init' in call for call in calls))
        self.assert_cleanup(root, calls)

    def test_final_refusal_still_fails_before_migration(self):
        root, result, calls = self.run_smoke('final-refusal')
        self.assertEqual(result.returncode, 1)
        self.assertIn('final TCP readiness check', result.stderr)
        self.assertFalse(any(call[0] == 'run' and 'anteroom-image-smoke-init' in call for call in calls))
        self.assert_cleanup(root, calls)

    def test_migration_failure_keeps_exit_code_and_logs_before_cleanup(self):
        root, result, calls = self.run_smoke('migration-failure')
        self.assertEqual(result.returncode, 42)
        self.assertFalse(any(call[0] == 'run' and 'anteroom-image-smoke-app' in call for call in calls))
        self.assert_cleanup(root, calls)


if __name__ == '__main__':
    unittest.main()
