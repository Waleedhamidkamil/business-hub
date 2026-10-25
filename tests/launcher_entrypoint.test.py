import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class LauncherEntrypoint(unittest.TestCase):
    def test_direct_script_entrypoint_loads_as_documented(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "launcher" / "launcher.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Business Hub on an office PC", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
