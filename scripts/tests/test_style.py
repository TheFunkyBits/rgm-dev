"""Behavioral checks for source EOF formatting and transactional replacement."""

from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

OWNER_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OWNER_ROOT / "scripts"))
import rgm_style as style

TOOLS = Path(os.environ.get("RGM_STYLE_TOOLS", Path.home() / ".cache" / "rgm-style-tools"))
SYNTHETIC_POLICY = {
    "version": 1,
    "protectedPaths": {
        "titles/example/src/*/resources/**": "Synthetic protected resource",
        "**/src/test/resources/**": "Synthetic protected fixture",
        "**/fixtures/**": "Synthetic protected fixture",
        "gradle/wrapper/**": "Original wrapper",
        ".nojekyll": "Contractually empty marker",
        "build/**": "Generated outputs",
    },
    "binaryExtensions": [".jar", ".png"],
}


class StyleTextTest(unittest.TestCase):
    def test_inventory_omits_deleted_tracked_paths_and_keeps_the_renamed_worktree_file(self):
        git = Path(shutil.which("git"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kept = root / "kept.md"
            removed = root / "removed.md"
            marker = root / ".nojekyll"
            kept.write_bytes(b"kept\n")
            removed.write_bytes(b"renamed\n")
            marker.write_bytes(b"")
            for arguments in (
                [git, "init", "--quiet"],
                [git, "add", "--", "kept.md", "removed.md", ".nojekyll"],
            ):
                result = style.run_process(arguments, cwd=root)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            removed.rename(root / "renamed.md")
            (root / "new.py").write_bytes(b"value = 1\n")

            eligible, excluded = style.inventory(root, git, SYNTHETIC_POLICY)

            self.assertEqual({"kept.md", "renamed.md", "new.py"}, {item["path"] for item in eligible})
            self.assertEqual([{"path": ".nojekyll", "reason": "Contractually empty marker"}], excluded)
            self.assertFalse(removed.exists())
            self.assertEqual(kept.read_bytes(), b"kept\n")

    def test_python_without_terminator_uses_the_owning_lf_or_crlf_convention(self):
        executables = style.verified_tools(TOOLS)
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=newline):
                source = style.decode_text(b'"""operator tooling"""', newline)
                formatted = style.format_text(
                    OWNER_ROOT,
                    {"formatter": "python", "path": "example.py"},
                    source,
                    TOOLS,
                    executables,
                )
                self.assertEqual(formatted, '"""operator tooling"""' + newline)
                self.assertEqual(
                    style.format_text(
                        OWNER_ROOT,
                        {"formatter": "python", "path": "example.py"},
                        style.decode_text(formatted.encode("utf-8"), newline),
                        TOOLS,
                        executables,
                    ),
                    formatted,
                )

    def test_semantic_preservation_rejects_changed_values(self):
        for kind, original, formatted in (
            ("python", "value = 1\n", "value = 2\n"),
            ("json", '{"value": 1}', '{"value": 2}'),
            ("toml", "value = 1", "value = 2"),
            ("xml", "<root>first</root>", "<root>second</root>"),
        ):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "parsed content"):
                    style.preserve_semantics(kind, original, formatted, Path("unused"))

    def test_installed_python_and_json_routes_are_read_only(self):
        tools = TOOLS
        self.assertTrue(
            (tools / "toolchain.json").is_file(), "Run the explicit owner setup before engine qualification"
        )
        root = OWNER_ROOT
        executables = style.verified_tools(tools)
        for kind, relative, text in (
            ("python", "example.py", "value= [1,2]\r\n"),
            ("json", "example.json", '{"value":[1,2]}\r\n\r\n'),
        ):
            with self.subTest(kind=kind):
                source = style.decode_text(text.encode("utf-8"), "\r\n")
                item = {"formatter": kind, "path": relative}
                formatted = style.format_text(root, item, source, tools, executables)
                style.preserve_semantics(kind, text, formatted, tools)
                self.assertNotEqual(formatted, text)
                self.assertTrue(formatted.endswith("\r\n"))
                repeated = style.format_text(
                    root, item, style.decode_text(formatted.encode("utf-8"), "\r\n"), tools, executables
                )
                self.assertEqual(repeated, formatted)
                self.assertFalse((root / relative).exists())

    def test_installed_xml_toml_yaml_and_cmake_routes(self):
        tools = TOOLS
        self.assertTrue((tools / "toolchain.json").is_file(), "Run explicit setup before qualification")
        root = OWNER_ROOT
        executables = style.verified_tools(tools)
        for kind, relative, text in (
            ("xml", "example.xml", '<root><child value="kept"> kept </child></root>\r\n'),
            ("toml", "example.toml", 'value=[1,2]\r\n'),
            ("yaml", "example.yml", 'value: [1,2]\r\n'),
            ("cmake", "CMakeLists.txt", 'set(VALUE  first second)\r\n'),
        ):
            with self.subTest(kind=kind):
                source = style.decode_text(text.encode("utf-8"), "\r\n")
                item = {"formatter": kind, "path": relative}
                result = style.format_text(root, item, source, tools, executables)
                self.assertTrue(result.endswith("\r\n"))
                repeated = style.format_text(
                    root, item, style.decode_text(result.encode("utf-8"), "\r\n"), tools, executables
                )
                self.assertEqual(result, repeated)

    def test_yaml_significant_eof_is_not_silently_changed(self):
        tools = TOOLS
        self.assertTrue((tools / "toolchain.json").is_file(), "Run explicit setup before qualification")
        original = "value: |+\n  content\n\n"
        with self.assertRaisesRegex(ValueError, "parsed content"):
            style.preserve_semantics("yaml", original, style.final_newline(original, "\n"), tools)

    def test_installed_cpp_preserves_include_order_and_literals(self):
        tools = TOOLS
        root = OWNER_ROOT
        self.assertTrue((root / ".clang-format").is_file())
        self.assertTrue((tools / "toolchain.json").is_file(), "Run explicit setup before qualification")
        text = '#include "z.h"\r\n#include "a.h"\r\nconst char* value="kept  ";\r\n'
        source = style.decode_text(text.encode("utf-8"), "\r\n")
        result = style.format_text(
            root, {"formatter": "cpp", "path": "example.cpp"}, source, tools, style.verified_tools(tools)
        )
        self.assertLess(result.index('"z.h"'), result.index('"a.h"'))
        self.assertIn('"kept  "', result)
        self.assertTrue(result.endswith("\r\n"))

    def test_source_classification_and_protected_inputs(self):
        for path, expected in (
            ("build-logic/src/main/kotlin/Example.kt", "kotlin"),
            ("titles/example/build.gradle.kts", "kotlin"),
            ("app/src/main/res/values/strings.xml", "xml"),
            ("native/host/CMakeLists.txt", "cmake"),
            (".gitignore", "eof"),
            ("titles/example/src/main/resources/Profile.json", None),
            ("core/src/test/resources/golden.json", None),
            ("scripts/release/fixtures/manifest.xml", None),
            ("gradle/wrapper/gradle-wrapper.properties", None),
            (".nojekyll", None),
            ("build/style/temporary.kt", None),
        ):
            with self.subTest(path=path):
                formatter, reason = style.classify(path, SYNTHETIC_POLICY)
                self.assertEqual(formatter, expected)
                self.assertEqual(reason is not None, expected is None)

    def test_missing_target_cpp_config_never_inherits_a_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "target"
            root.mkdir()
            (parent / ".clang-format").write_text("BasedOnStyle: Microsoft\n", encoding="utf-8")
            source = style.decode_text(b"int value=1;\n", "\n")
            with self.assertRaises(OSError):
                style.format_text(
                    root,
                    {"formatter": "cpp", "path": "source.cpp"},
                    source,
                    TOOLS,
                    style.verified_tools(TOOLS),
                )

    def test_cpp_parent_inheritance_and_unsafe_config_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".clang-format").write_text("BasedOnStyle: InheritParentConfig\n", encoding="utf-8")
            source = style.decode_text(b"int value=1;\n", "\n")
            with self.assertRaisesRegex(ValueError, "parent inheritance"):
                style.format_text(
                    root,
                    {"formatter": "cpp", "path": "source.cpp"},
                    source,
                    TOOLS,
                    style.verified_tools(TOOLS),
                )

    def test_final_newline_cases(self):
        for source, expected in (
            ("", "\n"),
            ("value", "value\n"),
            ("value\n", "value\n"),
            ("value\n\n", "value\n"),
            ("value  \n\t\n", "value  \n"),
            ("value\r\n\r\n", "value\r\n"),
        ):
            with self.subTest(source=source):
                newline = "\r\n" if "\r\n" in source else "\n"
                result = style.final_newline(source, newline)
                self.assertEqual(result, expected)
                self.assertEqual(style.final_newline(result, newline), result)

    def test_preserve_encoding_bom_and_line_endings(self):
        for prefix, encoding in ((b"", "utf-8"), (b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le")):
            with self.subTest(encoding=encoding, prefix=prefix):
                original = prefix + "value  \r\n\r\n".encode(encoding)
                source = style.decode_text(original, "\n")
                self.assertEqual(source.newline, "\r\n")
                self.assertEqual(
                    source.encode(style.final_newline(source.text, source.newline)),
                    prefix + "value  \r\n".encode(encoding),
                )

    def test_empty_file_uses_owning_convention(self):
        source = style.decode_text(b"", "\r\n")
        self.assertEqual(source.encode(style.final_newline(source.text, source.newline)), b"\r\n")

    def test_mixed_endings_and_invalid_encoding_are_rejected(self):
        for source in (b"first\r\nsecond\n", b"first\rsecond\r", b"\x80"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    style.decode_text(source, "\n")

    def test_atomic_apply_and_concurrent_change_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_bytes(b"value")
            style.apply_changes(root, {"source.txt": (b"value", b"value\n")})
            self.assertEqual(source.read_bytes(), b"value\n")
            source.write_bytes(b"concurrent")
            with self.assertRaisesRegex(RuntimeError, "Concurrent"):
                style.apply_changes(root, {"source.txt": (b"value\n", b"value")})
            self.assertEqual(source.read_bytes(), b"concurrent")

    def test_failure_rolls_back_prior_replacements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            writer = style.write_bytes_atomic

            def fail_second(path, source):
                if path == second and source == b"second\n":
                    raise OSError("injected failure")
                writer(path, source)

            with patch.object(style, "write_bytes_atomic", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "injected"):
                    style.apply_changes(
                        root, {"first.txt": (b"first", b"first\n"), "second.txt": (b"second", b"second\n")}
                    )
            self.assertEqual(first.read_bytes(), b"first")
            self.assertEqual(second.read_bytes(), b"second")
            self.assertEqual(list((root / "build" / "style").iterdir()), [])

    def test_escape_and_duplicate_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "repo"
            root.mkdir()
            outside = parent / "outside.txt"
            outside.write_bytes(b"outside")
            with self.assertRaisesRegex(ValueError, "outside"):
                style.apply_changes(root, {"../outside.txt": (b"outside", b"changed")})
            self.assertEqual(outside.read_bytes(), b"outside")

    def test_concurrent_edit_blocks_rollback_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            writer = style.write_bytes_atomic

            def fail_after_concurrent_edit(path, source):
                if path == second and source == b"second\n":
                    first.write_bytes(b"concurrent")
                    raise OSError("injected concurrent failure")
                writer(path, source)

            with patch.object(style, "write_bytes_atomic", side_effect=fail_after_concurrent_edit):
                with self.assertRaisesRegex(RuntimeError, "journal retained"):
                    style.apply_changes(
                        root, {"first.txt": (b"first", b"first\n"), "second.txt": (b"second", b"second\n")}
                    )
            self.assertEqual(first.read_bytes(), b"concurrent")
            self.assertEqual(second.read_bytes(), b"second")
            with self.assertRaisesRegex(RuntimeError, "inspection"):
                style.apply_changes(root, {"first.txt": (b"concurrent", b"replacement")})
            self.assertEqual(first.read_bytes(), b"concurrent")

    def test_check_apply_selection_nonmutation_and_idempotence(self):
        tools = TOOLS
        self.assertTrue((tools / "toolchain.json").is_file(), "Run explicit setup before qualification")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ruff.toml").write_bytes((OWNER_ROOT / ".ruff.toml").read_bytes())
            source = root / "sample.py"
            plain = root / "plain.txt"
            marker = root / ".nojekyll"
            source.write_bytes(b"value=1\n")
            plain.write_bytes(b"kept  \n\n")
            marker.write_bytes(b"")
            runner = style.run_process

            def transport(arguments, *, cwd, stdin=None):
                if arguments[0] == Path("fake-git"):
                    if "--deleted" in arguments:
                        output = b""
                    else:
                        output = (
                            b"sample.py\0plain.txt\0.nojekyll\0" if arguments[1] == "ls-files" else b"false\n"
                        )
                    return style.subprocess.CompletedProcess(arguments, 0, output, b"")
                return runner(arguments, cwd=cwd, stdin=stdin)

            with patch.object(style, "run_process", side_effect=transport):
                checked = style.style(
                    root, Path("fake-git"), tools, apply=False, skip_kotlin=False, policy=SYNTHETIC_POLICY
                )
                self.assertEqual(checked["changed"], 2)
                self.assertEqual(source.read_bytes(), b"value=1\n")
                self.assertEqual(plain.read_bytes(), b"kept  \n\n")
                applied = style.style(
                    root, Path("fake-git"), tools, apply=True, skip_kotlin=False, policy=SYNTHETIC_POLICY
                )
                self.assertEqual(applied["paths"], checked["paths"])
                self.assertEqual(source.read_bytes(), b"value = 1\n")
                self.assertEqual(plain.read_bytes(), b"kept  \n")
                self.assertEqual(marker.read_bytes(), b"")
                repeated = style.style(
                    root, Path("fake-git"), tools, apply=False, skip_kotlin=False, policy=SYNTHETIC_POLICY
                )
                self.assertEqual(repeated["changed"], 0)


if __name__ == "__main__":
    unittest.main()
