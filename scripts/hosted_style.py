"""Fixed hosted style phases with distinct owner, target and tool-cache roots."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import rgm_style


def selected_roots(
    action_path: Path, workspace: Path, runner_temp: Path, target: str, cache: str, smoke: str, kotlin: str
) -> dict:
    if kotlin not in ("true", "false"):
        raise ValueError("Kotlin input must be exactly true or false")
    if any(not value or "\n" in value or "\r" in value for value in (target, cache, smoke)):
        raise ValueError("Hosted style inputs must be nonempty single-line selections")
    workspace = workspace.resolve(strict=True)
    owner = action_path.resolve(strict=True).parents[1]
    if action_path.name != "style" or action_path.parent.name != "actions":
        raise ValueError("Hosted style must run from the owned action directory")
    target_root = (workspace / target).resolve(strict=True)
    if not target_root.is_relative_to(workspace) or not target_root.is_dir():
        raise ValueError("Hosted target must be a confined workspace directory")
    git = Path(shutil.which("git") or "")
    target_root = rgm_style.target_root(target_root, git)
    rgm_style.load_policy(target_root)
    smoke_root = (target_root / smoke).resolve(strict=True)
    if not smoke_root.is_relative_to(target_root) or not smoke_root.is_dir():
        raise ValueError("Hosted smoke root must be confined to the target")
    if not list(smoke_root.glob("test_style_routing.py")):
        raise ValueError("Target-owned style routing smoke tests are missing")
    cache_root = (workspace / cache).resolve()
    if not any(cache_root.is_relative_to(root.resolve(strict=True)) for root in (workspace, runner_temp)):
        raise ValueError("Hosted tool cache must remain in the workspace or runner temporary root")
    for left, right in ((owner, target_root), (cache_root, owner), (cache_root, target_root)):
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise ValueError("Owner, target and tool-cache roots must not overlap")
    eligible, _ = rgm_style.inventory(target_root, git, rgm_style.load_policy(target_root))
    if not eligible:
        raise ValueError("Hosted target has no eligible style inputs")
    if kotlin == "false" and any(item["formatter"] == "kotlin" for item in eligible):
        raise ValueError("A target with eligible Kotlin requires kotlin=true")
    return {"owner": owner, "target": target_root, "cache": cache_root, "smoke": smoke_root, "kotlin": kotlin}


def command(phase: str, roots: dict) -> list[str]:
    scripts = roots["owner"] / "scripts"
    if phase == "setup":
        return [sys.executable, "-B", str(scripts / "style_setup.py"), "--tool-root", str(roots["cache"])]
    if phase == "smoke":
        return [
            sys.executable,
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(roots["smoke"]),
            "-p",
            "test_style_routing.py",
        ]
    if phase == "check":
        return [
            sys.executable,
            "-B",
            str(scripts / "style.py"),
            "check",
            "--root",
            str(roots["target"]),
            "--policy",
            str(roots["target"] / "config/style.json"),
            "--tool-root",
            str(roots["cache"]),
        ]
    raise ValueError("Hosted style has no such execution phase")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("validate", "setup", "smoke", "check"))
    arguments = parser.parse_args()
    try:
        roots = selected_roots(
            Path(os.environ["GITHUB_ACTION_PATH"]),
            Path(os.environ["GITHUB_WORKSPACE"]),
            Path(os.environ["RUNNER_TEMP"]),
            os.environ["RGM_STYLE_TARGET"],
            os.environ["RGM_STYLE_CACHE"],
            os.environ["RGM_STYLE_SMOKE"],
            os.environ["RGM_STYLE_KOTLIN"],
        )
        environment = os.environ.copy()
        environment["RGM_DEV_ROOT"] = str(roots["owner"])
        environment["RGM_STYLE_TOOLS"] = str(roots["cache"])
        if arguments.phase == "validate":
            with Path(os.environ["GITHUB_ENV"]).open("a", encoding="utf-8", newline="\n") as output:
                output.write(f"RGM_DEV_ROOT={roots['owner']}\nRGM_STYLE_TOOLS={roots['cache']}\n")
            print(json.dumps({"version": 1, "kotlin": roots["kotlin"], "validated": True}))
            return 0
        return subprocess.run(
            command(arguments.phase, roots), cwd=roots["target"], env=environment, shell=False, check=False
        ).returncode
    except (ValueError, OSError, KeyError) as failure:
        detail = (
            str(failure) if isinstance(failure, ValueError) else "Required hosted input is missing or unsafe"
        )
        print(f"Hosted style refused: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
