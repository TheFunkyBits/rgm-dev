"""Source-text formatting with confined, rollback-capable replacement."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from xml.etree import ElementTree
from tool_versions import VERSIONS


def write_bytes_atomic(path: Path, source: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_process(arguments, *, cwd: Path, stdin: bytes | None = None):
    if isinstance(arguments, (str, bytes)) or not arguments:
        raise ValueError("Process arguments must be a nonempty sequence")
    return subprocess.run(
        [os.fspath(argument) for argument in arguments],
        cwd=cwd,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )


FORMATTERS = {
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".py": "python",
    ".cpp": "cpp",
    ".h": "cpp",
    ".cmake": "cmake",
    ".toml": "toml",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
}


def load_policy(root: Path, policy_path: Path | None = None) -> dict:
    selected = policy_path or root / "config" / "style.json"
    selected = selected.resolve(strict=True)
    if not selected.is_relative_to(root.resolve(strict=True)) or not selected.is_file():
        raise ValueError("Style policy must be an ordinary target-owned file")
    policy = json.loads(selected.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(policy, dict)
        or set(policy) != {"version", "protectedPaths", "binaryExtensions"}
        or type(policy["version"]) is not int
        or policy["version"] != 1
    ):
        raise ValueError("Unsupported or malformed style policy")
    if not isinstance(policy["protectedPaths"], dict) or not isinstance(policy["binaryExtensions"], list):
        raise ValueError("Style policy requires protected-path reasons and binary extensions")
    for pattern, reason in policy["protectedPaths"].items():
        if (
            not isinstance(pattern, str)
            or not pattern
            or pattern.startswith("/")
            or "\\" in pattern
            or ":" in pattern
            or ".." in pattern.split("/")
        ):
            raise ValueError("Style policy path must be target-relative")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Every protected style path requires an owning reason")
    if any(
        not isinstance(extension, str)
        or not extension.startswith(".")
        or "/" in extension
        or "\\" in extension
        for extension in policy["binaryExtensions"]
    ):
        raise ValueError("Invalid binary extension selection")
    return policy


def target_root(selected: Path, git: Path) -> Path:
    root = selected.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Style target must be a directory")
    result = run_process([git, "rev-parse", "--show-toplevel"], cwd=root)
    if result.returncode or Path(result.stdout.decode("utf-8").strip()).resolve() != root:
        raise ValueError("Style target must be the explicit Git worktree root")
    return root


def classify(relative: str, policy: dict) -> tuple[str | None, str | None]:
    for pattern, reason in policy["protectedPaths"].items():
        if fnmatchcase(relative, pattern):
            return None, reason
    suffix = Path(relative).suffix.lower()
    if suffix in policy["binaryExtensions"]:
        return None, "Binary or original fixture material"
    if Path(relative).name == "CMakeLists.txt":
        return "cmake", None
    if Path(relative).name in (".clang-format", ".gersemirc"):
        return "yaml", None
    return FORMATTERS.get(suffix, "eof"), None


def inventory(root: Path, git: Path, policy: dict) -> tuple[list[dict], list[dict]]:
    result = run_process([git, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
    if result.returncode:
        raise RuntimeError("Git source inventory failed")
    deleted = run_process([git, "ls-files", "-z", "--deleted"], cwd=root)
    if deleted.returncode:
        raise RuntimeError("Git deleted-source inventory failed")
    paths = sorted(
        set(result.stdout.decode("utf-8").split("\0"))
        - set(deleted.stdout.decode("utf-8").split("\0"))
        - {""}
    )
    config = run_process([git, "config", "--get", "core.autocrlf"], cwd=root)
    default_newline = "\r\n" if config.stdout.strip() == b"true" else "\n"
    eligible, excluded = [], []
    for relative in paths:
        formatter, reason = classify(relative, policy)
        if reason:
            excluded.append({"path": relative, "reason": reason})
            continue
        path = confined_file(root, relative)
        source = decode_text(path.read_bytes(), default_newline)
        eligible.append(
            {
                "path": relative,
                "formatter": formatter,
                "newline": "crlf" if source.newline == "\r\n" else "lf",
                "encoding": source.encoding,
            }
        )
    return eligible, excluded


@dataclass(frozen=True)
class TextFile:
    text: str
    encoding: str
    prefix: bytes
    newline: str

    def encode(self, text: str) -> bytes:
        return self.prefix + text.encode(self.encoding)


def decode_text(source: bytes, default_newline: str) -> TextFile:
    prefix = b""
    encoding = "utf-8"
    for marker, candidate in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if source.startswith(marker):
            prefix, encoding = marker, candidate
            source = source[len(marker) :]
            break
    text = source.decode(encoding, errors="strict")
    endings = set()
    for line in text.splitlines(keepends=True):
        if line.endswith("\r\n"):
            endings.add("\r\n")
        elif line.endswith("\n"):
            endings.add("\n")
        elif line.endswith("\r"):
            raise ValueError("Lone CR line endings require an owning decision")
    if len(endings) > 1:
        raise ValueError("Mixed line endings require an owning decision")
    return TextFile(text, encoding, prefix, next(iter(endings), default_newline))


def final_newline(text: str, newline: str) -> str:
    lines = text.splitlines(keepends=True)
    while lines and not lines[-1].strip(" \t\r\n"):
        lines.pop()
    if not lines:
        return newline
    if not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    return "".join(lines)


def confined_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError(f"Source is outside repository: {relative}")
    if not resolved.is_file() or candidate.is_symlink():
        raise ValueError(f"Source is not an ordinary file: {relative}")
    if resolved.stat().st_nlink > 1:
        raise ValueError(f"Source has shared hard links: {relative}")
    return resolved


def apply_changes(root: Path, changes: dict[str, tuple[bytes, bytes]]) -> None:
    if not changes:
        return
    root = root.resolve(strict=True)
    journal_parent = root / "build" / "style"
    if not journal_parent.resolve().is_relative_to(root):
        raise ValueError("Journal directory is outside repository")
    if journal_parent.is_dir() and any(journal_parent.glob("transaction-*")):
        raise RuntimeError("An unresolved formatting transaction requires inspection before replay")
    destinations = {name: confined_file(root, name) for name in changes}
    if len(set(destinations.values())) != len(destinations):
        raise ValueError("Source paths must have distinct destinations")
    for name, (original, _) in changes.items():
        if destinations[name].read_bytes() != original:
            raise RuntimeError(f"Concurrent source change: {name}")
    journal_parent.mkdir(parents=True, exist_ok=True)
    journal = Path(tempfile.mkdtemp(prefix="transaction-", dir=journal_parent))
    modes = {name: stat.S_IMODE(path.stat().st_mode) for name, path in destinations.items()}
    manifest = []
    for index, (name, (original, formatted)) in enumerate(changes.items()):
        write_bytes_atomic(journal / f"{index}.original", original)
        write_bytes_atomic(journal / f"{index}.formatted", formatted)
        manifest.append({"path": name, "mode": modes[name], "index": index})
    write_bytes_atomic(journal / "journal.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    committed = []
    try:
        for name, (original, formatted) in changes.items():
            destination = confined_file(root, name)
            if destination != destinations[name] or destination.read_bytes() != original:
                raise RuntimeError(f"Concurrent source change: {name}")
            write_bytes_atomic(destination, formatted)
            committed.append(name)
            os.chmod(destination, modes[name])
    except BaseException:
        conflicted = []
        for name in reversed(committed):
            original, formatted = changes[name]
            destination = destinations[name]
            if confined_file(root, name) != destination or destination.read_bytes() != formatted:
                conflicted.append(name)
                continue
            write_bytes_atomic(destination, original)
            os.chmod(destination, modes[name])
        if conflicted:
            raise RuntimeError(f"Rollback blocked by concurrent edits; journal retained: {journal}")
        shutil.rmtree(journal)
        raise
    shutil.rmtree(journal)


def python_tool(tools: Path, module: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        "-c",
        "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module(sys.argv.pop(1),run_name='__main__')",
        str(tools / "python"),
        module,
    ]


def verified_tools(tools: Path) -> dict[str, Path]:
    tools = tools.resolve(strict=True)
    manifest = json.loads((tools / "toolchain.json").read_text(encoding="utf-8"))
    if manifest["versions"] != VERSIONS:
        raise ValueError("Formatter versions differ; run the explicit style setup")
    windows = sys.platform == "win32"
    node_directory = f"node-v{VERSIONS['node']}-{'win-x64' if windows else 'linux-x64'}"
    executables = {
        "node": tools / node_directory / ("node.exe" if windows else "bin/node"),
        "clang": tools
        / "python"
        / "clang_format"
        / "data"
        / "bin"
        / ("clang-format.exe" if windows else "clang-format"),
        "taplo": tools / ("taplo.exe" if windows else "taplo"),
        "prettier": tools / "node_modules" / "prettier" / "bin" / "prettier.cjs",
        "xml": tools / "node_modules" / "@prettier" / "plugin-xml" / "src" / "plugin.js",
    }
    for name, path in executables.items():
        if not path.is_file() or not path.resolve().is_relative_to(tools):
            raise ValueError(f"Formatter executable is unavailable or escapes tool root: {name}")
    return executables


def xml_value(text: str):
    def value(element):
        return (
            element.tag,
            dict(element.attrib),
            element.text if element.text and element.text.strip() else None,
            [(value(child), child.tail if child.tail and child.tail.strip() else None) for child in element],
        )

    return value(ElementTree.fromstring(text))


def yaml_value(source: str, tools: Path):
    previous = list(sys.path)
    try:
        sys.path.insert(0, str(tools / "python"))
        import yaml

        return yaml.safe_load(source)
    finally:
        sys.path[:] = previous


def preserve_semantics(kind: str, original: str, formatted: str, tools: Path) -> None:
    if kind == "python":
        before = ast.dump(ast.parse(original), include_attributes=False)
        after = ast.dump(ast.parse(formatted), include_attributes=False)
    elif kind == "json":
        before, after = json.loads(original), json.loads(formatted)
    elif kind == "toml":
        before, after = tomllib.loads(original), tomllib.loads(formatted)
    elif kind == "xml":
        before, after = xml_value(original), xml_value(formatted)
    elif kind == "yaml":
        before, after = yaml_value(original, tools), yaml_value(formatted, tools)
    else:
        return
    if before != after:
        raise ValueError(f"{kind} formatter changed parsed content")


def format_text(root: Path, item: dict, source: TextFile, tools: Path, executables: dict) -> str:
    kind, relative = item["formatter"], item["path"]
    path = root / relative
    if kind in ("eof", "kotlin"):
        return final_newline(source.text, source.newline)
    if kind == "python":
        arguments = python_tool(tools, "ruff") + [
            "format",
            "--no-cache",
            "--config",
            str(confined_file(root, ".ruff.toml")),
            "--config",
            'format.line-ending="' + ("cr-lf" if source.newline == "\r\n" else "lf") + '"',
            "--stdin-filename",
            str(path),
            "-",
        ]
    elif kind in ("html", "css", "json", "yaml", "markdown", "xml"):
        arguments = [
            str(executables["node"]),
            str(executables["prettier"]),
            "--config",
            str(confined_file(root, ".prettierrc.json")),
            "--no-editorconfig",
            "--stdin-filepath",
            str(path),
            "--end-of-line",
            "crlf" if source.newline == "\r\n" else "lf",
        ]
        if kind == "xml":
            arguments += [
                "--plugin",
                str(executables["xml"]),
                "--xml-whitespace-sensitivity",
                "strict",
                "--xml-sort-attributes-by-key",
                "false",
                "--xml-quote-attributes",
                "preserve",
            ]
    elif kind == "cpp":
        settings = yaml_value(confined_file(root, ".clang-format").read_text(encoding="utf-8"), tools)
        if not isinstance(settings, dict) or settings.get("BasedOnStyle") == "InheritParentConfig":
            raise ValueError(
                "C++ formatting requires an explicit target-owned configuration without parent inheritance"
            )
        settings["LineEnding"] = "CRLF" if source.newline == "\r\n" else "LF"
        arguments = [
            str(executables["clang"]),
            "--assume-filename",
            str(path),
            "--fail-on-incomplete-format",
            "--style",
            json.dumps(settings),
        ]
    elif kind == "toml":
        arguments = [
            str(executables["taplo"]),
            "fmt",
            "--no-auto-config",
            "--colors",
            "never",
            "--option",
            "reorder_keys=false",
            "--option",
            "reorder_arrays=false",
            "--option",
            "reorder_inline_tables=false",
            "--option",
            "column_width=110",
            "--option",
            "crlf=" + ("true" if source.newline == "\r\n" else "false"),
            "-",
        ]
    elif kind == "cmake":
        parent = root / "build" / "style"
        if not parent.resolve().is_relative_to(root.resolve()):
            raise ValueError("Formatter staging directory is outside repository")
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cmake-", dir=parent) as directory:
            staged = Path(directory) / "CMakeLists.txt"
            write_bytes_atomic(staged, source.text.encode("utf-8"))
            configuration = root / ".gersemirc"
            if configuration.exists():
                configuration = confined_file(root, ".gersemirc")
            else:
                configuration = Path(directory) / ".gersemirc"
                write_bytes_atomic(configuration, b"{}\n")
            arguments = python_tool(tools, "gersemi") + [
                "--config",
                str(configuration),
                "--safe",
                "--no-cache",
                "--line-length",
                "110",
                "--indent",
                "4",
                "--in-place",
                str(staged),
            ]
            result = run_process(arguments, cwd=root)
            if result.returncode:
                raise RuntimeError(
                    f"CMake formatter failed: {result.stderr.decode('utf-8', errors='replace')}"
                )
            formatted = staged.read_bytes().decode("utf-8", errors="strict")
        observed = decode_text(formatted.encode("utf-8"), source.newline)
        if observed.newline != source.newline:
            raise ValueError(f"CMake formatter changed line endings: {relative}")
        return final_newline(formatted, source.newline)
    else:
        raise ValueError(f"Formatter route is not yet configured: {kind}")
    result = run_process(arguments, cwd=root, stdin=source.text.encode("utf-8"))
    if result.returncode:
        raise RuntimeError(
            f"Formatter failed for {relative}: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    formatted = result.stdout.decode("utf-8", errors="strict")
    observed = decode_text(formatted.encode("utf-8"), source.newline)
    if observed.newline != source.newline:
        raise ValueError(f"Formatter changed the owning line-ending convention: {relative}")
    formatted = final_newline(formatted, source.newline)
    preserve_semantics(kind, source.text, formatted, tools)
    return formatted


def format_kotlin(root: Path, entries: list[dict], tools: Path) -> dict[str, str]:
    if not entries:
        return {}
    java = shutil.which("java")
    jar = tools / f"ktfmt-{VERSIONS['ktfmt']}-with-dependencies.jar"
    if not java or not jar.is_file():
        raise ValueError("JDK 17 and the explicitly provisioned ktfmt jar are required")
    observed = run_process([java, "-version"], cwd=root)
    if observed.returncode or 'version "17.' not in observed.stderr.decode("utf-8", errors="replace"):
        raise ValueError("The formatter requires JDK 17")
    parent = root / "build" / "style"
    if not parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("Kotlin staging directory is outside repository")
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kotlin-", dir=parent) as directory:
        staged = Path(directory)
        write_bytes_atomic(staged / ".editorconfig", confined_file(root, ".editorconfig").read_bytes())
        paths = []
        for item in entries:
            relative = item["path"]
            original = confined_file(root, relative)
            target = staged / relative
            if not target.resolve().is_relative_to(staged):
                raise ValueError("Kotlin target escapes staging")
            write_bytes_atomic(target, original.read_bytes())
            paths.append(str(target))
        command = [
            java,
            "-jar",
            str(jar),
            "--kotlinlang-style",
            "--enable-editorconfig",
            "--do-not-remove-unused-imports",
        ]
        batch, length = [], 0
        for path in paths + [""]:
            if batch and (not path or length + len(path) > 18000):
                result = run_process(command + batch, cwd=staged)
                if result.returncode:
                    raise RuntimeError(
                        "Kotlin formatter failed: " + result.stderr.decode("utf-8", errors="replace")
                    )
                batch, length = [], 0
            if path:
                batch.append(path)
                length += len(path) + 3
        for convergence_attempt in range(3):
            before = {path: Path(path).read_bytes().decode("utf-8") for path in paths}
            batch, length = [], 0
            for path in paths + [""]:
                if batch and (not path or length + len(path) > 18000):
                    result = run_process(command + batch, cwd=staged)
                    if result.returncode:
                        raise RuntimeError(
                            "Kotlin formatter failed: " + result.stderr.decode("utf-8", errors="replace")
                        )
                    batch, length = [], 0
                if path:
                    batch.append(path)
                    length += len(path) + 3
            if all(Path(path).read_bytes().decode("utf-8") == before[path] for path in paths):
                break
        else:
            raise ValueError("Kotlin formatter output did not converge in staged files")
        return {
            item["path"]: (staged / item["path"]).read_bytes().decode("utf-8", errors="strict")
            for item in entries
        }


def style(
    root: Path, git: Path, tools: Path, *, apply: bool, skip_kotlin: bool, policy: dict | None = None
) -> dict:
    journal_parent = root / "build" / "style"
    if not journal_parent.resolve().is_relative_to(root.resolve()):
        raise ValueError("Formatting state is outside repository")
    if journal_parent.is_dir() and any(journal_parent.glob("transaction-*")):
        raise RuntimeError(
            "An unresolved formatting transaction requires inspection before checking or replay"
        )
    eligible, excluded = inventory(root, git, policy or load_policy(root))
    if not eligible:
        raise ValueError("Style selection has no eligible source inputs")
    executables = verified_tools(tools)
    kotlin = (
        format_kotlin(root, [item for item in eligible if item["formatter"] == "kotlin"], tools)
        if not skip_kotlin
        else {}
    )
    changes = {}
    for item in eligible:
        path = confined_file(root, item["path"])
        original = path.read_bytes()
        source = decode_text(original, "\r\n" if item["newline"] == "crlf" else "\n")
        if item["path"] in kotlin:
            output = kotlin[item["path"]]
            observed = decode_text(output.encode("utf-8"), source.newline)
            if observed.newline != source.newline:
                raise ValueError(f"Kotlin formatter changed line endings: {item['path']}")
            output = final_newline(output, source.newline)
        else:
            output = format_text(root, item, source, tools, executables)
        formatted = source.encode(output)
        if original != formatted:
            changes[item["path"]] = (original, formatted)
    if apply:
        apply_changes(root, changes)
    return {
        "eligible": len(eligible),
        "excluded": len(excluded),
        "changed": len(changes),
        "paths": list(changes),
        "applied": apply,
        "kotlin_layout_skipped": skip_kotlin,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "check", "apply"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--git", type=Path, default=Path(shutil.which("git") or "git"))
    parser.add_argument(
        "--tool-root",
        type=Path,
        default=Path(os.environ.get("RGM_STYLE_TOOLS", Path.home() / ".cache" / "rgm-style-tools")),
    )
    parser.add_argument("--skip-kotlin", action="store_true")
    arguments = parser.parse_args()
    root = target_root(arguments.root, arguments.git)
    policy = load_policy(root, arguments.policy)
    if arguments.command != "inventory":
        outcome = style(
            root,
            arguments.git,
            arguments.tool_root,
            apply=arguments.command == "apply",
            skip_kotlin=arguments.skip_kotlin,
            policy=policy,
        )
        print(json.dumps(outcome, indent=2))
        return 1 if arguments.command == "check" and outcome["changed"] else 0
    eligible, excluded = inventory(root, arguments.git, policy)
    counts = {}
    for item in eligible:
        counts[item["formatter"]] = counts.get(item["formatter"], 0) + 1
    reasons = {}
    for item in excluded:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    print(
        json.dumps(
            {
                "eligible": len(eligible),
                "excluded": len(excluded),
                "formatters": counts,
                "exclusion_reasons": reasons,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
