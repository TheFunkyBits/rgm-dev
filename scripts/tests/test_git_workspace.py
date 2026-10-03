"""Synthetic independent repositories and source-only Git delivery contracts."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import git_workspace as delivery


class GitWorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.executable = Path(shutil.which("git")).resolve()
        self.git = delivery.Git(self.executable)
        self.selection = {"repositories": []}
        self.command(self.workspace, "init", "--quiet", "--initial-branch=main")

    def command(self, root, *arguments):
        return self.git.text(root, *arguments)

    def repository(self, label):
        root = self.workspace / label
        root.mkdir()
        remote = self.base / f"{label}.git"
        self.command(self.base, "init", "--quiet", "--bare", str(remote))
        self.command(root, "init", "--quiet", "--initial-branch=main")
        self.command(root, "config", "user.name", "Synthetic Git Operator")
        self.command(root, "config", "user.email", "operator@example.invalid")
        self.command(root, "config", "core.autocrlf", "false")
        (root / "source.txt").write_text("original\n", encoding="utf-8", newline="\n")
        self.command(root, "add", "--", "source.txt")
        self.command(root, "commit", "--quiet", "-m", "synthetic initial source")
        self.command(root, "remote", "add", "origin", remote.as_posix())
        self.command(root, "push", "--quiet", "--no-follow-tags", "origin", "HEAD:refs/heads/main")
        entry = {
            "label": label,
            "root": label,
            "remote": remote.as_posix(),
            "branch": "main",
            "head": self.command(root, "rev-parse", "HEAD"),
            "outgoing": [],
            "paths": [],
            "message": "Update synthetic source",
        }
        self.selection["repositories"].append(entry)
        return root, entry

    def route(self, selection=None, git=None):
        return delivery.Workspace(self.workspace, selection or self.selection, git or self.git)

    def parent(self, leaves):
        root = self.workspace
        self.command(root, "config", "user.name", "Synthetic Git Operator")
        self.command(root, "config", "user.email", "operator@example.invalid")
        self.command(root, "config", "core.autocrlf", "false")
        remote = self.base / "parent.git"
        self.command(self.base, "init", "--quiet", "--bare", str(remote))
        modules = "".join(
            f'[submodule "{leaf}"]\n\tpath = {leaf}\n\turl = {(self.base / (leaf + ".git")).as_posix()}\n'
            for leaf in leaves
        )
        (root / ".gitmodules").write_text(modules, encoding="utf-8", newline="\n")
        (root / "parent.txt").write_text("original parent\n", encoding="utf-8", newline="\n")
        self.command(root, "add", "--", ".gitmodules", "parent.txt")
        for leaf in leaves:
            self.command(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{self.command(root / leaf, 'rev-parse', 'HEAD')},{leaf}",
            )
        self.command(root, "commit", "--quiet", "-m", "Synthetic parent selection")
        self.command(root, "remote", "add", "origin", remote.as_posix())
        self.command(root, "push", "--quiet", "--no-follow-tags", "origin", "HEAD:refs/heads/main")
        entry = {
            "label": "parent",
            "root": ".",
            "remote": remote.as_posix(),
            "branch": "main",
            "head": self.command(root, "rev-parse", "HEAD"),
            "outgoing": [],
            "paths": [{"path": "parent.txt", "stage": "worktree"}]
            + [{"path": leaf, "stage": "worktree"} for leaf in leaves],
            "message": "Update synthetic parent pins",
            "gitlinks": {leaf: leaf for leaf in leaves},
        }
        self.selection["repositories"].append(entry)
        return entry

    def test_status_preserves_spaces_unicode_and_both_rename_endpoints(self):
        changes = delivery.parse_status("R  new name.txt\0old name.txt\0?? caf\u00e9.py\0".encode("utf-8"))
        self.assertEqual(changes[0], delivery.Change("new name.txt", "R", " ", "old name.txt"))
        self.assertEqual(changes[1].path, "caf\u00e9.py")
        for invalid in (b"UU source.txt\0", b"R  renamed\0", b"?? ../outside\0", b"?? source.txt"):
            with self.subTest(status=invalid):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.parse_status(invalid)

    def test_read_only_review_checks_actual_bare_remote_and_preserves_the_index(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("reviewed\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        before = (root / ".git/index").read_bytes()

        result = self.route().review()

        self.assertEqual(result["status"], "reviewed")
        self.assertEqual(result["repositories"][0]["remoteHead"], entry["head"])
        self.assertEqual((root / ".git/index").read_bytes(), before)
        self.assertEqual(self.command(root, "diff", "--cached", "--name-only"), "")

    def test_unapproved_index_partial_staging_and_remote_mismatch_are_refused(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("first\n", encoding="utf-8", newline="\n")
        self.command(root, "add", "--", "source.txt")
        with self.assertRaisesRegex(delivery.DeliveryError, "Unapproved staged"):
            self.route().review()
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        (root / "source.txt").write_text("second\n", encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "Partially staged"):
            self.route().review()
        entry["paths"][0]["stage"] = "index"
        self.assertEqual(self.route().review()["status"], "reviewed")
        changed = copy.deepcopy(self.selection)
        changed["repositories"][0]["remote"] = "https://example.invalid/source.git"
        with self.assertRaisesRegex(delivery.DeliveryError, "destination differs"):
            self.route(changed).review()

    def test_process_arguments_have_no_shell_and_disable_interactive_credentials(self):
        root, _ = self.repository("child")
        with patch.object(
            delivery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"", b"")
        ) as invoked:
            git = delivery.Git(self.executable, runner=delivery.subprocess.run)
            git.call(root, "status", "--porcelain=v1", "-z")
        arguments = invoked.call_args.args[0]
        self.assertEqual(arguments[-3:], ["status", "--porcelain=v1", "-z"])
        self.assertFalse(invoked.call_args.kwargs["shell"])
        self.assertEqual(invoked.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(invoked.call_args.kwargs["env"]["GCM_INTERACTIVE"], "never")

    def test_dry_run_is_read_only_then_source_addition_deletion_and_unicode_paths_are_delivered(self):
        root, entry = self.repository("child")
        (root / "source.txt").unlink()
        name = "caf\u00e9 [literal].txt"
        (root / name).write_text("new source\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}, {"path": name, "stage": "worktree"}]
        route = self.route()
        index = (root / ".git/index").read_bytes()

        self.assertEqual(route.apply(dry_run=True)["status"], "dry-run")
        self.assertEqual((root / ".git/index").read_bytes(), index)
        self.assertFalse(route.control_path().exists())
        result = route.apply()

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(
            self.command(root, "ls-remote", "--refs", "origin", "refs/heads/main").split("\t")[0],
            result["repositories"][0]["commit"],
        )
        self.assertEqual(self.command(root, "status", "--porcelain=v1"), "")
        self.assertFalse(route.control_path().exists())

    def test_all_target_preflight_failure_prevents_any_commit_or_index_change(self):
        root, entry = self.repository("first")
        other, second = self.repository("second")
        (root / "source.txt").write_text("selected\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        (other / "source.txt").write_text("unapproved\n", encoding="utf-8", newline="\n")
        self.command(other, "add", "--", "source.txt")
        index = (root / ".git/index").read_bytes()

        with self.assertRaisesRegex(delivery.DeliveryError, "Unapproved staged"):
            self.route().apply()

        self.assertEqual(self.command(root, "rev-parse", "HEAD"), entry["head"])
        self.assertEqual((root / ".git/index").read_bytes(), index)
        self.assertFalse(self.route().control_path().exists())

    def test_clean_skip_and_approved_ahead_only_push_do_not_create_extra_commits(self):
        root, entry = self.repository("child")
        result = self.route().apply()
        self.assertEqual(result["repositories"][0]["status"], "skipped")
        (root / "source.txt").write_text("already committed\n", encoding="utf-8", newline="\n")
        self.command(root, "add", "--", "source.txt")
        self.command(root, "commit", "--quiet", "-m", "Reviewed existing commit")
        ahead = self.command(root, "rev-parse", "HEAD")
        entry["head"] = ahead
        entry["outgoing"] = [ahead]

        self.assertEqual(self.route().apply()["repositories"][0]["commit"], ahead)
        self.assertEqual(self.command(root, "rev-parse", "HEAD"), ahead)

    def test_index_only_delivery_preserves_unstaged_work(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("approved index\n", encoding="utf-8", newline="\n")
        self.command(root, "add", "--", "source.txt")
        (root / "source.txt").write_text("keep unstaged\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "index"}]

        result = self.route().apply()

        self.assertEqual(self.command(root, "show", "HEAD:source.txt"), "approved index")
        self.assertEqual((root / "source.txt").read_text(encoding="utf-8"), "keep unstaged\n")
        self.assertEqual(result["repositories"][0]["remaining"], ["source.txt"])

    def test_lost_push_ack_is_inspected_and_continued_without_replaying_the_push(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("delivered\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        runner = subprocess.run
        pushes = []

        def lose_ack(arguments, **kwargs):
            result = runner(arguments, **kwargs)
            if "push" in arguments:
                pushes.append(arguments)
                return subprocess.CompletedProcess(arguments, 1, b"", b"synthetic lost acknowledgement")
            return result

        route = self.route(git=delivery.Git(self.executable, runner=lose_ack))
        with self.assertRaisesRegex(delivery.DeliveryError, "Git push failed"):
            route.apply()
        self.assertEqual(route.inspect()["repositories"][0]["status"], "delivered")
        with self.assertRaisesRegex(delivery.DeliveryError, "explicit continuation"):
            route.apply()
        result = route.apply(continue_operation=True)

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(len(pushes), 1)
        self.assertFalse(route.control_path().exists())

    def test_hook_scope_expansion_stops_before_push_and_retains_the_commit_intent(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("selected\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        runner = subprocess.run
        pushed = []

        def injected_hook(arguments, **kwargs):
            if "commit" in arguments:
                (root / "hook.txt").write_text("not approved\n", encoding="utf-8", newline="\n")
                self.command(root, "add", "--", "hook.txt")
            if "push" in arguments:
                pushed.append(arguments)
            return runner(arguments, **kwargs)

        route = self.route(git=delivery.Git(self.executable, runner=injected_hook))
        with self.assertRaisesRegex(delivery.DeliveryError, "unapproved source changes"):
            route.apply()
        self.assertEqual(pushed, [])
        self.assertTrue(route.control_path().exists())
        self.assertEqual(self.git.remote_head(route.repositories[0]), entry["head"])

    def test_parent_pins_only_delivered_child_commits_and_waits_after_a_failed_child_push(self):
        for label in ("first", "second"):
            root, entry = self.repository(label)
            (root / "source.txt").write_text(label + " change\n", encoding="utf-8", newline="\n")
            entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        parent = self.parent(["first", "second"])
        (self.workspace / "parent.txt").write_text("reviewed parent\n", encoding="utf-8", newline="\n")
        parent_index = (self.workspace / ".git/index").read_bytes()
        runner = subprocess.run
        calls = []
        reject = [True]

        def transport(arguments, **kwargs):
            action = arguments[arguments.index("-C") + 2]
            if action in ("commit", "push"):
                calls.append((Path(kwargs["cwd"]).name, action))
            if action == "push" and Path(kwargs["cwd"]).name == "first" and reject[0]:
                return subprocess.CompletedProcess(arguments, 1, b"", b"synthetic rejected push")
            return runner(arguments, **kwargs)

        route = self.route(git=delivery.Git(self.executable, runner=transport))
        with self.assertRaisesRegex(delivery.DeliveryError, "Git push failed"):
            route.apply()
        self.assertEqual(self.command(self.workspace, "rev-parse", "HEAD"), parent["head"])
        self.assertEqual((self.workspace / ".git/index").read_bytes(), parent_index)
        self.assertNotIn(("workspace", "commit"), calls)
        self.assertEqual(route.inspect()["repositories"][0]["status"], "not-pushed")
        reject[0] = False
        result = route.apply(continue_operation=True)

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(calls[-2:], [("workspace", "commit"), ("workspace", "push")])
        for label in ("first", "second"):
            self.assertEqual(
                self.command(self.workspace, "rev-parse", f"HEAD:{label}"),
                self.command(self.workspace / label, "rev-parse", "HEAD"),
            )

    def test_ignored_retired_gitlink_deletion_is_delivered_without_tracking_the_retained_directory(self):
        for label in ("child", "extra"):
            self.repository(label)
        parent = self.parent(["child", "extra"])
        self.command(self.workspace, "update-index", "--force-remove", "extra")
        modules = f'[submodule "child"]\n\tpath = child\n\turl = {(self.base / "child.git").as_posix()}\n'
        (self.workspace / ".gitmodules").write_text(modules, encoding="utf-8", newline="\n")
        (self.workspace / ".gitignore").write_text("extra/\n", encoding="utf-8", newline="\n")
        parent["gitlinks"] = {"child": "child"}
        parent["paths"] = [
            {"path": "child", "stage": "worktree"},
            {"path": "extra", "stage": "index"},
            {"path": ".gitmodules", "stage": "worktree"},
            {"path": ".gitignore", "stage": "worktree"},
        ]

        result = self.route().apply()

        self.assertEqual(result["status"], "delivered")
        self.assertEqual(self.command(self.workspace, "ls-tree", "HEAD", "extra"), "")
        self.assertTrue((self.workspace / "extra/.git").is_dir())
        self.assertEqual(
            self.command(self.workspace / "extra", "rev-parse", "HEAD"),
            self.selection["repositories"][1]["head"],
        )

    def test_remote_drift_after_preflight_stops_before_commit(self):
        root, entry = self.repository("child")
        (root / "source.txt").write_text("selected\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        runner = subprocess.run
        observations = [0]

        def drift(arguments, **kwargs):
            result = runner(arguments, **kwargs)
            if "ls-remote" in arguments:
                observations[0] += 1
                if observations[0] > 1:
                    return subprocess.CompletedProcess(arguments, 0, b"f" * 40 + b"\trefs/heads/main\n", b"")
            return result

        with self.assertRaises(delivery.DeliveryError):
            self.route(git=delivery.Git(self.executable, runner=drift)).apply()
        self.assertEqual(self.command(root, "rev-parse", "HEAD"), entry["head"])
        self.assertEqual(self.command(root, "diff", "--cached", "--name-only"), "")

    def test_ignored_inputs_and_shared_source_hard_links_are_refused(self):
        root, entry = self.repository("child")
        (root / ".gitignore").write_text("private.txt\n", encoding="utf-8", newline="\n")
        (root / "private.txt").write_text("synthetic ignored input\n", encoding="utf-8", newline="\n")
        entry["paths"] = [{"path": "private.txt", "stage": "worktree"}]
        with self.assertRaisesRegex(delivery.DeliveryError, "Ignored source"):
            self.route().review()
        entry["paths"] = [{"path": "source.txt", "stage": "worktree"}]
        import os

        os.link(root / "source.txt", root / "shared.txt")
        with self.assertRaisesRegex(delivery.DeliveryError, "shared hard links"):
            self.route().review()


if __name__ == "__main__":
    unittest.main()
