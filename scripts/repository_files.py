"""Read-only repository hygiene using one explicit target-owned policy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value or ":" in value:
        raise ValueError("Repository path must be relative and use slash separators")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("Repository path has an unsafe segment")
    return value


def local_file(root: Path, selected: Path) -> Path:
    resolved = selected.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file() or selected.is_symlink():
        raise ValueError("Repository policy/allowlist must remain an ordinary target-owned file")
    return resolved


def load_policy(root: Path, selected: Path) -> dict:
    policy = json.loads(local_file(root, selected).read_text(encoding="utf-8-sig"))
    if not isinstance(policy, dict) or set(policy) != {
        "version",
        "bannedExtensions",
        "skipDirectoryNames",
        "exactAllowlistFile",
    }:
        raise ValueError("Malformed repository-files policy")
    if type(policy["version"]) is not int or policy["version"] != 1:
        raise ValueError("Unsupported repository-files policy version")
    for key in ("bannedExtensions", "skipDirectoryNames"):
        if not isinstance(policy[key], list) or any(
            not isinstance(value, str) or not value for value in policy[key]
        ):
            raise ValueError("Repository policy selections must be nonempty strings")
        if len(policy[key]) != len(set(policy[key])):
            raise ValueError("Repository policy repeats a selection")
    if any(not re.fullmatch(r"[a-z0-9]+", value) for value in policy["bannedExtensions"]):
        raise ValueError("Banned extensions must be lowercase last-dot extensions")
    if any(
        "/" in value or "\\" in value or ":" in value or value in (".", "..")
        for value in policy["skipDirectoryNames"]
    ):
        raise ValueError("Skipped directories must be single case-sensitive names")
    if policy["exactAllowlistFile"] is not None:
        relative_path(policy["exactAllowlistFile"])
    return policy


def extension(name: str) -> str:
    return name.rsplit(".", 1)[1].lower() if "." in name else ""


def exact_allowlist(root: Path, policy: dict) -> set[str]:
    name = policy["exactAllowlistFile"]
    if name is None:
        return set()
    selected = local_file(root, root / name)
    allowed, folded = set(), set()
    for line in selected.read_text(encoding="utf-8-sig").splitlines():
        value = line.partition("#")[0].strip()
        if not value:
            continue
        relative_path(value)
        if value.casefold() in folded:
            raise ValueError("Repository allowlist repeats a case-folded path")
        if extension(Path(value).name) not in policy["bannedExtensions"]:
            raise ValueError("Repository allowlist names a non-banned extension")
        try:
            local_file(root, root / value)
        except (ValueError, OSError) as failure:
            raise ValueError(f"Allowlisted repository file is missing or unsafe: {value}") from failure
        folded.add(value.casefold())
        allowed.add(value)
    return allowed


def check(root: Path, policy: dict) -> list[str]:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Repository target must be a directory")
    allowed = exact_allowlist(root, policy)
    skipped = set(policy["skipDirectoryNames"])
    banned = set(policy["bannedExtensions"])
    offending = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as children:
            for entry in children:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False) and entry.name in skipped:
                    continue
                resolved = path.resolve(strict=True)
                relative = path.relative_to(root).as_posix()
                reparse = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & getattr(
                    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
                )
                if not resolved.is_relative_to(root) or entry.is_symlink() or reparse:
                    raise ValueError(f"Unsafe reached repository entry: {relative}")
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    if extension(entry.name) in banned and relative not in allowed:
                        offending.append(relative)
                else:
                    raise ValueError(f"Uninspectable repository entry: {relative}")

    visit(root)
    return sorted(offending)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check",))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        root = arguments.root.resolve(strict=True)
        policy = load_policy(root, arguments.policy)
        offending = check(root, policy)
        print(
            json.dumps({"version": 1, "status": "blocked" if offending else "clean", "offenders": offending})
        )
        return 1 if offending else 0
    except (ValueError, OSError) as failure:
        detail = (
            str(failure)
            if isinstance(failure, ValueError)
            else "Input or traversal could not be inspected safely"
        )
        print(f"Repository-files check refused: {type(failure).__name__}: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
