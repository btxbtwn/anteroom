"""Exercise the real cloud hook without network, signing keys or Apple services."""
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

IOS = Path(__file__).resolve().parents[1]


class CloudSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cloud setup ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ios = self.root / "anteroom-ios"
        for directory in ("ci_scripts", "tools", "Config", "Sources"):
            (self.ios / directory).mkdir(parents=True)
        (self.ios / "Sources/PrivacyInfo.xcprivacy").write_bytes(plistlib.dumps({}))
        shutil.copy(IOS / "ci_scripts/ci_post_clone.sh", self.ios / "ci_scripts")
        self.write_tool("tools/install-xcodegen.sh", '''#!/bin/bash
set -eu
mkdir -p "$1/xcodegen/bin"
printf '#!/bin/bash\\ntouch generated-project\\n' > "$1/xcodegen/bin/xcodegen"
chmod +x "$1/xcodegen/bin/xcodegen"
''')
        self.write_tool("tools/test-models.sh", "#!/bin/bash\ntouch model-tests-passed\n")
        self.env = {**os.environ, "CI_PRIMARY_REPOSITORY_PATH": str(self.root),
                    "CI_BUILD_NUMBER": "123", "CI_XCODEBUILD_ACTION": "build-for-testing"}
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        plutil = bin_dir / "plutil"
        plutil.write_text('#!/bin/sh\ntest "$1" = -lint && test -f "$2"\n')
        plutil.chmod(0o755)
        self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        self.env.pop("SUPABASE_PUBLISHABLE_KEY", None)

    def write_tool(self, name, content):
        path = self.ios / name
        path.write_text(content)
        path.chmod(0o755)

    def run_hook(self):
        return subprocess.run(["bash", str(self.ios / "ci_scripts/ci_post_clone.sh")],
                              check=False, cwd="/tmp", env=self.env, capture_output=True, text=True)

    def test_generates_project_and_build_number_from_another_directory(self):
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.ios / "model-tests-passed").exists())
        self.assertTrue((self.ios / "generated-project").exists())
        self.assertIn("CURRENT_PROJECT_VERSION = 123", (self.ios / "Config/Cloud.xcconfig").read_text())

    def test_archive_requires_publishable_key(self):
        self.env["CI_XCODEBUILD_ACTION"] = "archive"
        self.assertNotEqual(self.run_hook().returncode, 0)
        self.assertFalse((self.ios / "generated-project").exists())

    def test_archive_writes_public_key(self):
        self.env.update(CI_XCODEBUILD_ACTION="archive", SUPABASE_PUBLISHABLE_KEY="sb_publishable_aaaaaaaa")
        result = self.run_hook()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SUPABASE_PUBLISHABLE_KEY = sb_publishable_aaaaaaaa", (self.ios / "Config/Cloud.xcconfig").read_text())

    def test_rejects_invalid_settings_before_running_tools(self):
        for name, value in (("CI_BUILD_NUMBER", "0"), ("CI_BUILD_NUMBER", "123\nBAD=1"),
                            ("SUPABASE_PUBLISHABLE_KEY", "sb_secret_test")):
            with self.subTest(name=name, value=value):
                previous = self.env.get(name)
                self.env[name] = value
                self.assertNotEqual(self.run_hook().returncode, 0)
                self.assertFalse((self.ios / "generated-project").exists())
                if previous is None:
                    del self.env[name]
                else:
                    self.env[name] = previous

    def test_installer_failure_stops_generation(self):
        self.write_tool("tools/install-xcodegen.sh", "#!/bin/bash\nexit 9\n")
        self.assertEqual(self.run_hook().returncode, 9)
        self.assertFalse((self.ios / "generated-project").exists())

    def test_failed_model_checks_stop_generation(self):
        self.write_tool("tools/test-models.sh", "#!/bin/bash\nexit 7\n")
        self.assertEqual(self.run_hook().returncode, 7)
        self.assertFalse((self.ios / "generated-project").exists())


if __name__ == "__main__":
    unittest.main()
