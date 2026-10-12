"""Regression tests ensuring old video-generation/remix/acquisition entry points stay disabled."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = {
    "content_pipeline/build.py": "VIDEO_CREATION_DISABLED",
    "content_pipeline/comedy_pipeline.py": "VIDEO_REMIX_DISABLED",
    "content_pipeline/iran_video_hunter.py": "LEGACY_VIDEO_HUNTER_DISABLED",
}


class DisabledLegacyEntrypointTests(unittest.TestCase):
    def test_legacy_entrypoints_fail_closed_without_network_or_output(self):
        for script, marker in ENTRYPOINTS.items():
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, str(ROOT / script)],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                output = (result.stdout or "") + (result.stderr or "")
                self.assertNotEqual(result.returncode, 0, f"{script} unexpectedly ran")
                self.assertIn(marker, output, f"{script} did not fail closed clearly")


if __name__ == "__main__":
    unittest.main(verbosity=2)
