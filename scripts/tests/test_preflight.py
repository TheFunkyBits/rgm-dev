"""Synthetic lookup-mode and fixed-probe preflight contracts."""

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import preflight


class PreflightTest(unittest.TestCase):
    def test_fixed_metadata_probe_and_identity_fields(self):
        output = (
            b"java.version = 17.0.10\njava.vendor = Fixture\njava.vm.name = VM\njava.vm.version = 17.0.10+1\n"
        )
        with patch.object(
            preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", output)
        ) as invoked:
            result = preflight.jdk_identity(Path("java"), Path("project"))
        self.assertEqual(
            result,
            {
                "feature": 17,
                "version": "17.0.10",
                "vendor": "Fixture",
                "vmName": "VM",
                "vmVersion": "17.0.10+1",
            },
        )
        self.assertEqual(invoked.call_args.args[0], ["java", "-XshowSettings:properties", "-version"])
        self.assertFalse(invoked.call_args.kwargs["shell"])

    def test_feature_mismatch_and_missing_version_are_refused(self):
        for output in (b"java.version = 21.0.1", b"java.vendor = Fixture"):
            with self.subTest(output=output):
                with patch.object(
                    preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", output)
                ):
                    with self.assertRaises(ValueError):
                        preflight.jdk_identity(Path("java"), Path("project"))

    def test_invalid_set_home_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {"JAVA_HOME": directory}),
                patch.object(preflight.shutil, "which", return_value=sys.executable) as searched,
            ):
                with self.assertRaises(OSError):
                    preflight.select_java("java-home-or-path", None)
                searched.assert_not_called()

    def test_path_mode_ignores_home_and_optional_home_falls_back(self):
        with (
            patch.dict(os.environ, {"JAVA_HOME": "invalid"}),
            patch.object(preflight.shutil, "which", return_value=sys.executable),
        ):
            self.assertEqual(preflight.select_java("path", None), Path(sys.executable).resolve())
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(preflight.shutil, "which", return_value=sys.executable),
        ):
            self.assertEqual(preflight.select_java("java-home-or-path", None), Path(sys.executable).resolve())
            with self.assertRaisesRegex(ValueError, "JAVA_HOME"):
                preflight.select_java("java-home", None)

    def test_explicit_selection_and_wrapper_confinement(self):
        self.assertEqual(
            preflight.select_java("explicit", Path(sys.executable)), Path(sys.executable).resolve()
        )
        with self.assertRaises(ValueError):
            preflight.select_java("explicit", Path("relative"))
        with tempfile.TemporaryDirectory(prefix="project with spaces ") as directory:
            root = Path(directory)
            wrapper = root / "gradle" / "wrapper.jar"
            wrapper.parent.mkdir()
            wrapper.write_bytes(b"fixture")
            self.assertEqual(
                preflight.wrapper_input(root, "gradle/wrapper.jar"), (root.resolve(), wrapper.resolve())
            )
            for relative in ("../outside.jar", "/absolute.jar", "gradle\\wrapper.jar", "gradle//wrapper.jar"):
                with self.subTest(relative=relative):
                    with self.assertRaises(ValueError):
                        preflight.wrapper_input(root, relative)

    def test_cli_success_is_versioned_json_with_explicit_inputs(self):
        with tempfile.TemporaryDirectory(prefix="non sibling project ") as directory:
            root = Path(directory)
            (root / "wrapper.jar").write_bytes(b"fixture")
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(preflight.__file__)),
                    "--executable",
                    f"python={sys.executable}",
                    "--project-root",
                    str(root),
                    "--wrapper",
                    "wrapper.jar",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=False,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            accepted = json.loads(result.stdout)
            self.assertEqual(accepted["version"], 1)
            self.assertEqual(accepted["executables"], {"python": Path(sys.executable).resolve().as_posix()})
            self.assertEqual(Path(accepted["wrapper"]), root.resolve() / "wrapper.jar")
            self.assertEqual(result.stderr, "")
            self.assertEqual((root / "wrapper.jar").read_bytes(), b"fixture")

    def test_cli_failure_emits_no_accepted_result(self):
        for arguments in (
            ["--contract", "2"],
            ["--project-root", str(Path.cwd())],
            ["--path-executable", "../java"],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, "-B", str(Path(preflight.__file__)), *arguments],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    shell=False,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("Preflight refused", result.stderr)


if __name__ == "__main__":
    unittest.main()
