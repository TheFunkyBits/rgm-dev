"""Synthetic policy differences and fail-closed hygiene controls."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import repository_files


def policy(banned=("bin", "exe", "gog"), skipped=("build", ".git"), allowlist=None):
    return {
        "version": 1,
        "bannedExtensions": list(banned),
        "skipDirectoryNames": list(skipped),
        "exactAllowlistFile": allowlist,
    }


class RepositoryFilesTest(unittest.TestCase):
    def test_case_extension_size_and_ignored_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "small.EXE").write_bytes(b"x")
            (root / "large.txt").write_bytes(b"x" * 8192)
            (root / ".gitignore").write_text("small.EXE\n", encoding="utf-8")
            (root / ".exe").write_bytes(b"x")
            before = (root / "small.EXE").read_bytes()
            self.assertEqual(repository_files.check(root, policy()), [".exe", "small.EXE"])
            self.assertEqual((root / "small.EXE").read_bytes(), before)

    def test_nested_skip_names_and_content_gog_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "source" / "build"
            nested.mkdir(parents=True)
            (nested / "ignored.exe").write_bytes(b"x")
            (root / "owned.gog").write_bytes(b"x")
            self.assertEqual(repository_files.check(root, policy()), ["owned.gog"])
            self.assertEqual(repository_files.check(root, policy(banned=("bin", "exe"))), [])

    def test_exact_allowlist_and_case_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "owned.bin").write_bytes(b"x")
            selected = root / "allowlist.txt"
            selected.write_text("# synthetic\nowned.bin # accepted\n", encoding="utf-8")
            self.assertEqual(repository_files.check(root, policy(allowlist="allowlist.txt")), [])
            selected.write_text("*.bin\n", encoding="utf-8")
            with self.assertRaises((ValueError, OSError)):
                repository_files.check(root, policy(allowlist="allowlist.txt"))
            selected.write_text("owned.bin\nOWNED.BIN\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "case-folded"):
                repository_files.check(root, policy(allowlist="allowlist.txt"))

    def test_malformed_stale_and_non_banned_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ordinary.txt").write_text("kept", encoding="utf-8")
            selected = root / "allowlist.txt"
            for value in ("../escape.bin", "bad\\name.bin", "missing.bin", "ordinary.txt", "/absolute.bin"):
                with self.subTest(value=value):
                    selected.write_text(value + "\n", encoding="utf-8")
                    with self.assertRaises((ValueError, OSError)):
                        repository_files.check(root, policy(allowlist="allowlist.txt"))

    def test_owner_policy_and_unsupported_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / "policy.json"
            selected.write_text(json.dumps(policy()), encoding="utf-8")
            self.assertEqual(repository_files.load_policy(root.resolve(), selected), policy())
            changed = policy()
            changed["version"] = 2
            selected.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                repository_files.load_policy(root.resolve(), selected)

    def test_authoring_tools_directory_remains_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / ".tools"
            tools.mkdir()
            (tools / "unexpected.exe").write_bytes(b"x")
            self.assertEqual(repository_files.check(root, policy()), [".tools/unexpected.exe"])
            self.assertEqual(repository_files.check(root, policy(skipped=("build", ".git", ".tools"))), [])

    def test_all_three_current_policy_vectors_cover_production_and_ignored_inputs(self):
        client_bans = (
            "lbx",
            "exe",
            "com",
            "img",
            "iso",
            "cue",
            "bin",
            "gog",
            "jks",
            "keystore",
            "p12",
            "pfx",
            "pepk",
        )
        client_skips = (
            ".git",
            ".gradle",
            ".idea",
            ".tools",
            "build",
            "out",
            ".kotlin",
            ".cxx",
            "node_modules",
        )
        authoring_bans = (
            "aab",
            "aar",
            "apk",
            "bin",
            "com",
            "cue",
            "exe",
            "gam",
            "gog",
            "img",
            "iso",
            "jar",
            "jks",
            "keystore",
            "lbx",
            "moo",
            "zip",
        )
        vectors = (
            (
                policy(client_bans, client_skips),
                ["tiny.gog", "titles/example/src/main/resources/private.EXE"],
            ),
            (
                policy(
                    tuple(value for value in client_bans if value != "gog"),
                    (".git", ".gradle", "build", ".idea"),
                ),
                [".tools/private.exe", "titles/example/src/main/resources/private.EXE"],
            ),
            (
                policy(authoring_bans, tuple(value for value in client_skips if value != ".tools")),
                [".tools/private.exe", "tiny.gog", "titles/example/src/main/resources/private.EXE"],
            ),
        )
        for selected, expected in vectors:
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in (
                    ".tools/private.exe",
                    "titles/example/src/main/resources/private.EXE",
                    "tiny.gog",
                    "nested/build/ignored.exe",
                ):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"x")
                (root / ".gitignore").write_text("titles/\n*.gog\n", encoding="utf-8")
                (root / "large-owned.dat").write_bytes(b"x" * 103425)
                self.assertEqual(repository_files.check(root, selected), expected)

    def test_unreadable_traversal_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                repository_files.os, "scandir", side_effect=PermissionError("synthetic denied")
            ):
                with self.assertRaises(PermissionError):
                    repository_files.check(Path(directory), policy())

    def test_resolved_external_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "target"
            root.mkdir()
            entry = root / "linked.bin"
            entry.write_bytes(b"x")
            outside = parent / "outside.bin"
            outside.write_bytes(b"private")
            resolver = Path.resolve

            def escaped(path, strict=False):
                return outside if path == entry else resolver(path, strict=strict)

            with patch.object(Path, "resolve", escaped):
                with self.assertRaisesRegex(ValueError, "Unsafe reached repository entry: linked.bin"):
                    repository_files.check(root, policy())
            self.assertEqual(outside.read_bytes(), b"private")


if __name__ == "__main__":
    unittest.main()
