"""Read-only executable, JDK 17 and wrapper preflight with explicit lookup semantics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def regular_file(selected: Path) -> Path:
    resolved = selected.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Selected executable/input is not an ordinary file")
    return resolved


def path_executable(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("PATH lookup requires an executable name, not a path")
    selected = shutil.which(name)
    if not selected:
        raise ValueError(f"Requested PATH executable is unavailable: {name}")
    return regular_file(Path(selected))


def select_java(mode: str, explicit: Path | None) -> Path:
    if mode == "explicit":
        if explicit is None or not explicit.is_absolute():
            raise ValueError("Explicit Java lookup requires an absolute --java path")
        return regular_file(explicit)
    if explicit is not None:
        raise ValueError("--java is valid only with explicit lookup")
    if mode == "path":
        return path_executable("java")
    home = os.environ.get("JAVA_HOME")
    if home:
        suffix = "java.exe" if sys.platform == "win32" else "java"
        return regular_file(Path(home) / "bin" / suffix)
    if mode == "java-home-or-path":
        return path_executable("java")
    raise ValueError("This lookup mode requires JAVA_HOME")


def jdk_identity(java: Path, cwd: Path) -> dict:
    result = subprocess.run(
        [str(java), "-XshowSettings:properties", "-version"],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        check=False,
    )
    if result.returncode:
        raise ValueError("The selected JDK metadata probe failed")
    properties = {}
    for line in (result.stdout + result.stderr).decode("utf-8", errors="replace").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            if name.strip():
                properties[name.strip()] = value.strip()
    version = properties.get("java.version")
    if not version:
        raise ValueError("JDK metadata omitted java.version")
    feature = int(version.split(".", 1)[0])
    if feature != 17:
        raise ValueError("The selected Java executable must be JDK 17")
    return {
        "feature": feature,
        "version": version,
        "vendor": properties.get("java.vendor", "unknown"),
        "vmName": properties.get("java.vm.name", "unknown"),
        "vmVersion": properties.get("java.vm.version", "unknown"),
    }


def wrapper_input(project: Path, relative: str) -> tuple[Path, Path]:
    root = project.resolve(strict=True)
    if (
        not root.is_dir()
        or not relative
        or Path(relative).is_absolute()
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError("Wrapper requires an explicit project and relative portable path")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("Wrapper path has an unsafe segment")
    wrapper = regular_file(root / relative)
    if not wrapper.is_relative_to(root):
        raise ValueError("Wrapper escapes the selected project")
    return root, wrapper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=int, default=1)
    parser.add_argument("--java-lookup", choices=("explicit", "java-home", "java-home-or-path", "path"))
    parser.add_argument("--java", type=Path)
    parser.add_argument("--executable", action="append", default=[])
    parser.add_argument("--path-executable", action="append", default=[])
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--wrapper")
    arguments = parser.parse_args()
    try:
        if arguments.contract != 1:
            raise ValueError("Unsupported preflight contract")
        if bool(arguments.project_root) != bool(arguments.wrapper):
            raise ValueError("Project root and wrapper selection must be paired")
        project, wrapper = (
            wrapper_input(arguments.project_root, arguments.wrapper) if arguments.wrapper else (None, None)
        )
        executables = {}
        for selection in arguments.executable:
            name, separator, value = selection.partition("=")
            if not separator or not name or name in executables or not Path(value).is_absolute():
                raise ValueError("Executable selection requires a unique name and absolute path")
            executables[name] = regular_file(Path(value)).as_posix()
        for name in arguments.path_executable:
            if not name or name in executables:
                raise ValueError("PATH executable selection must have a unique name")
            executables[name] = path_executable(name).as_posix()
        java = None
        if arguments.java_lookup:
            selected = select_java(arguments.java_lookup, arguments.java)
            java = {"path": selected.as_posix(), **jdk_identity(selected, project or Path.cwd())}
        elif arguments.java:
            raise ValueError("Java selection requires an explicit lookup mode")
        if not (java or executables or wrapper):
            raise ValueError("Preflight requires at least one selected input")
        print(
            json.dumps(
                {
                    "version": 1,
                    "java": java,
                    "executables": executables,
                    "projectRoot": project.as_posix() if project else None,
                    "wrapper": wrapper.as_posix() if wrapper else None,
                }
            )
        )
        return 0
    except (ValueError, OSError) as failure:
        detail = (
            str(failure)
            if isinstance(failure, ValueError)
            else "Selected input could not be inspected safely"
        )
        print(f"Preflight refused: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
