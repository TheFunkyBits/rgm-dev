"""Hosted action roots and fixed command controls using synthetic checkouts."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hosted_style


class HostedStyleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="hosted roots ")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.target = self.workspace / "separate target"
        self.owner = self.workspace / "public tools"
        self.action = self.owner / "actions/style"
        self.action.mkdir(parents=True)
        self.target.mkdir()
        (self.workspace / "runner temp").mkdir()
        self.runner_temp = self.workspace / "runner temp"
        (self.target / "config").mkdir()
        (self.target / "config/style.json").write_text(
            json.dumps({"version": 1, "protectedPaths": {}, "binaryExtensions": []}), encoding="utf-8"
        )
        (self.target / "tests").mkdir()
        (self.target / "tests/test_style_routing.py").write_text("fixture = True\n", encoding="utf-8")
        subprocess.run(
            [str(Path(shutil.which("git")).resolve()), "init", str(self.target)],
            capture_output=True,
            check=True,
            shell=False,
        )

    def roots(self, **changes):
        arguments = {
            "action_path": self.action,
            "workspace": self.workspace,
            "runner_temp": self.runner_temp,
            "target": "separate target",
            "cache": str(self.runner_temp / "tool cache"),
            "smoke": "tests",
            "kotlin": "false",
        }
        arguments.update(changes)
        return hosted_style.selected_roots(**arguments)

    def test_non_sibling_spaced_roots_are_explicit_and_validation_does_not_provision(self):
        before = list(self.owner.rglob("*"))
        roots = self.roots()
        self.assertEqual(roots["owner"], self.owner.resolve())
        self.assertEqual(roots["target"], self.target.resolve())
        self.assertEqual(roots["smoke"], (self.target / "tests").resolve())
        self.assertFalse(roots["cache"].exists())
        self.assertEqual(list(self.owner.rglob("*")), before)

    def test_overlap_escape_and_missing_smokes_are_refused(self):
        for changes in (
            {"cache": str(self.target / "cache")},
            {"cache": str(self.owner / "cache")},
            {"smoke": "../public tools"},
            {"smoke": "config"},
            {"target": ".."},
        ):
            with self.subTest(changes=changes), self.assertRaises((ValueError, OSError)):
                self.roots(**changes)

    def test_strict_boolean_and_kotlin_selection(self):
        with self.assertRaises(ValueError):
            self.roots(kotlin="yes")
        (self.target / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "kotlin=true"):
            self.roots()
        self.assertEqual(self.roots(kotlin="true")["kotlin"], "true")

    def test_commands_have_no_arbitrary_runner_apply_or_skip_kotlin(self):
        roots = self.roots()
        check = hosted_style.command("check", roots)
        self.assertIn("check", check)
        self.assertEqual(check[check.index("--root") + 1], str(self.target.resolve()))
        self.assertNotIn("--skip-kotlin", check)
        self.assertEqual(hosted_style.command("setup", roots)[3], "--tool-root")
        smoke = hosted_style.command("smoke", roots)
        self.assertEqual(smoke[-2:], ["-p", "test_style_routing.py"])
        for phase in ("apply", "run", "upload"):
            with self.assertRaises(ValueError):
                hosted_style.command(phase, roots)

    def test_action_yaml_exposes_only_fixed_validated_steps(self):
        tools = Path(os.environ.get("RGM_STYLE_TOOLS", Path.home() / ".cache/rgm-style-tools"))
        previous = list(sys.path)
        try:
            sys.path.insert(0, str(tools / "python"))
            import yaml

            owner = Path(__file__).resolve().parents[2]
            action = yaml.load(
                (owner / "actions/style/action.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
            workflow = yaml.load(
                (owner / ".github/workflows/check.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
            )
        finally:
            sys.path[:] = previous
        self.assertEqual(set(action["inputs"]), {"target-root", "tool-cache", "smoke-root", "kotlin"})
        self.assertTrue(all(value["required"] == "true" for value in action["inputs"].values()))
        steps = action["runs"]["steps"]
        self.assertEqual(action["runs"]["using"], "composite")
        self.assertEqual(
            [step["run"].rsplit(" ", 1)[1] for step in steps if "run" in step],
            ["validate", "setup", "smoke", "check"],
        )
        self.assertTrue(all(step["shell"] == "pwsh" for step in steps if "run" in step))
        java = next(step for step in steps if step.get("uses", "").startswith("actions/setup-java@"))
        self.assertEqual(java["if"], "inputs.kotlin == 'true'")
        self.assertEqual(java["with"]["java-version"], "17")
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"^actions/(?:setup-python|setup-java)@[0-9a-f]{40}$")
            self.assertNotIn("secrets", step)
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            workflow["jobs"]["check"]["strategy"]["matrix"]["os"], ["ubuntu-24.04", "windows-2025"]
        )
        self.assertNotIn("pull_request_target", workflow["on"])
        checkout = workflow["jobs"]["check"]["steps"][0]
        self.assertEqual(checkout["with"]["persist-credentials"], "false")


if __name__ == "__main__":
    unittest.main()
