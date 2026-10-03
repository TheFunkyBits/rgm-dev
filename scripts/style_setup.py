"""Explicit, development-only provisioning for the pinned source formatters."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import io
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

from rgm_style import write_bytes_atomic
from tool_versions import NPM_PACKAGES, VERSIONS


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "RGM-style-setup"})
    with urlopen(request, timeout=120) as response:
        return response.read()


def metadata(url: str) -> dict:
    return json.loads(fetch(url))


def destination(root: Path, relative: str) -> Path:
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("Tool package path escapes its installation root")
    return result


def install_kotlin(root: Path) -> Path:
    filename = f"ktfmt-{VERSIONS['ktfmt']}-with-dependencies.jar"
    target = root.resolve() / filename
    write_bytes_atomic(
        target,
        fetch(f"https://repo.maven.apache.org/maven2/com/facebook/ktfmt/{VERSIONS['ktfmt']}/{filename}"),
    )
    return target


def install_package(root: Path, name: str, installed: dict) -> None:
    if name in installed:
        return
    if not re.fullmatch(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+", name):
        raise ValueError("Invalid formatter package name")
    if name not in NPM_PACKAGES:
        raise ValueError(f"Formatter dependency has no reviewed version pin: {name}")
    manifest = metadata(f"https://unpkg.com/{name}@{quote(NPM_PACKAGES[name])}/package.json")
    exact_version = manifest["version"]
    if exact_version != NPM_PACKAGES[name] or manifest["name"] != name:
        raise ValueError(f"Formatter dependency selection differs: {name}")
    installed[name] = exact_version
    package_root = destination(root, "node_modules/" + name)
    package_root.mkdir(parents=True, exist_ok=True)
    listing = metadata(f"https://unpkg.com/{name}@{exact_version}/?meta")

    def download(item):
        relative = item["path"].lstrip("/")
        target = destination(package_root, relative)
        source = fetch(f"https://unpkg.com/{name}@{exact_version}/{quote(relative, safe='/')}")
        write_bytes_atomic(target, source)

    with ThreadPoolExecutor(max_workers=6) as workers:
        list(workers.map(download, listing["files"]))
    for dependency in manifest.get("dependencies", {}):
        install_package(root, dependency, installed)


def setup(root: Path) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    windows = sys.platform == "win32"
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("The qualified formatter toolchain requires an x86-64 host")
    python_root = root / "python"
    install_kotlin(root)
    command = [
        sys.executable,
        "-B",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-compile",
        "--only-binary=:all:",
        "--target",
        str(python_root),
    ]
    command += [f"{name}=={VERSIONS[name]}" for name in ("ruff", "clang-format", "gersemi")]
    command.append("pyyaml==6.0.3")
    subprocess.run(command, check=True, shell=False, stdout=sys.stderr, stderr=sys.stderr)
    node_name = f"node-v{VERSIONS['node']}-{'win-x64' if windows else 'linux-x64'}"
    if windows:
        archive = zipfile.ZipFile(
            io.BytesIO(fetch(f"https://nodejs.org/dist/v{VERSIONS['node']}/{node_name}.zip"))
        )
        for entry in archive.infolist():
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Tool archive contains a symbolic link")
            target = destination(root, entry.filename)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                write_bytes_atomic(target, archive.read(entry))
        node = root / node_name / "node.exe"
    else:
        import tarfile

        archive = tarfile.open(
            fileobj=io.BytesIO(fetch(f"https://nodejs.org/dist/v{VERSIONS['node']}/{node_name}.tar.gz")),
            mode="r:gz",
        )
        for entry in archive.getmembers():
            target = destination(root, entry.name)
            if entry.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif entry.isfile():
                reader = archive.extractfile(entry)
                if reader is None:
                    raise ValueError("Tool archive member has no contents")
                write_bytes_atomic(target, reader.read())
                os.chmod(target, entry.mode)
            elif entry.issym() and entry.name.endswith(("bin/npm", "bin/npx", "bin/corepack")):
                continue
            else:
                raise ValueError("Tool archive contains an unsupported member")
        node = root / node_name / "bin" / "node"
    release = metadata(f"https://api.github.com/repos/tamasfe/taplo/releases/tags/{VERSIONS['taplo']}")
    asset_name = f"taplo-{'windows' if windows else 'linux'}-x86_64.gz"
    asset = next(item for item in release["assets"] if item["name"] == asset_name)
    taplo = root / ("taplo.exe" if windows else "taplo")
    write_bytes_atomic(taplo, gzip.decompress(fetch(asset["browser_download_url"])))
    if not windows:
        os.chmod(taplo, 0o755)
    packages = {}
    for name in ("prettier", "@prettier/plugin-xml"):
        install_package(root, name, packages)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(python_root)
    checks = {
        "ruff": [sys.executable, "-B", "-m", "ruff", "--version"],
        "clang-format": [
            str(
                python_root
                / "clang_format"
                / "data"
                / "bin"
                / ("clang-format.exe" if windows else "clang-format")
            ),
            "--version",
        ],
        "gersemi": [sys.executable, "-B", "-m", "gersemi", "--version"],
        "taplo": [str(taplo), "--version"],
        "node": [str(node), "--version"],
        "prettier": [
            str(node),
            str(root / "node_modules" / "prettier" / "bin" / "prettier.cjs"),
            "--version",
        ],
    }
    observations = {}
    for name, arguments in checks.items():
        result = subprocess.run(
            arguments,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            shell=False,
        )
        observations[name] = result.stdout.decode("utf-8").strip()
        if VERSIONS[name] not in observations[name]:
            raise ValueError(f"Installed tool version differs: {name}")
    manifest = {"versions": VERSIONS, "packages": packages, "observations": observations}
    write_bytes_atomic(root / "toolchain.json", (json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tool-root",
        type=Path,
        default=Path(os.environ.get("RGM_STYLE_TOOLS", Path.home() / ".cache" / "rgm-style-tools")),
    )
    parser.add_argument("--kotlin-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.kotlin_only:
        target = install_kotlin(arguments.tool_root)
        print(json.dumps({"ktfmt": VERSIONS["ktfmt"], "installed": target.is_file()}))
        return 0
    print(json.dumps(setup(arguments.tool_root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
