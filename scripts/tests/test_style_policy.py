"""Explicit owner/target policy contract controls, using no private inputs."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rgm_style


class StylePolicyTest(unittest.TestCase):
    def test_cli_rejects_implicit_owner_target(self):
        entry = Path(__file__).resolve().parents[1] / "style.py"
        result = subprocess.run(
            [sys.executable, "-B", str(entry), "inventory"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"--root", result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_target_owned_policy_and_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            selected = config / "style.json"
            selected.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "protectedPaths": {"fixtures/**": "Owned fixture"},
                        "binaryExtensions": [".png"],
                    }
                ),
                encoding="utf-8",
            )
            policy = rgm_style.load_policy(root)
            self.assertEqual(rgm_style.classify("source/example.py", policy), ("python", None))
            self.assertEqual(rgm_style.classify("fixtures/example.json", policy), (None, "Owned fixture"))
            self.assertEqual(rgm_style.classify("image.png", policy)[0], None)
            self.assertEqual(rgm_style.classify("other", policy), ("eof", None))

    def test_invalid_version_and_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "target"
            root.mkdir()
            outside = parent / "policy.json"
            outside.write_text(
                json.dumps({"version": 1, "protectedPaths": {}, "binaryExtensions": []}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "target-owned"):
                rgm_style.load_policy(root, outside)
            inside = root / "policy.json"
            for policy in (
                {"version": 2, "protectedPaths": {}, "binaryExtensions": []},
                {"version": True, "protectedPaths": {}, "binaryExtensions": []},
                [],
                {"version": 1, "protectedPaths": {"../other": "Escape"}, "binaryExtensions": []},
                {"version": 1, "protectedPaths": {"file": ""}, "binaryExtensions": []},
            ):
                inside.write_text(json.dumps(policy), encoding="utf-8")
                with self.assertRaises(ValueError):
                    rgm_style.load_policy(root, inside)


if __name__ == "__main__":
    unittest.main()
