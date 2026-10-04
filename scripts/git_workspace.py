"""Explicit, source-only Git delivery across independent worktrees."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


class DeliveryError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise DeliveryError(message)


def unique_object(pairs):
    document = {}
    for key, value in pairs:
        require(key not in document, "Selection/control repeats a JSON field")
        document[key] = value
    return document


def encode_baseline(snapshot):
    return {
        "head": snapshot["head"],
        "index": base64.b64encode(snapshot["index"]).decode("ascii"),
        "sources": {
            path: base64.b64encode(source).decode("ascii") if source is not None else None
            for path, source in snapshot["sources"].items()
        },
    }


def decode_baseline(document):
    require(
        type(document) is dict and set(document) == {"head", "index", "sources"},
        "Malformed captured source/index state",
    )
    require(
        isinstance(document["head"], str) and REVISION.fullmatch(document["head"]), "Captured HEAD is invalid"
    )
    require(
        isinstance(document["index"], str) and type(document["sources"]) is dict,
        "Malformed captured index/source",
    )
    sources = {}
    for path, source in document["sources"].items():
        portable(path)
        require(source is None or isinstance(source, str), "Captured source is malformed")
        sources[path] = base64.b64decode(source, validate=True) if source is not None else None
    return {
        "head": document["head"],
        "index": base64.b64decode(document["index"], validate=True),
        "sources": sources,
    }


def canonical(path: Path, *, directory=False, exclusive=False):
    require(path.is_absolute(), "Selected roots and executables must be absolute")
    info = path.lstat()
    require(
        not path.is_symlink()
        and path.resolve(strict=True) == path
        and not getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
        "Selected input is linked, nonregular or noncanonical",
    )
    if exclusive and not directory:
        require(info.st_nlink == 1, "Selected input has shared hard links")
    return path


def portable(value, *, root=False):
    require(isinstance(value, str) and value, "Selection requires portable relative paths")
    if root and value == ".":
        return value
    require(
        not PurePosixPath(value).is_absolute()
        and "\\" not in value
        and ":" not in value
        and "\0" not in value
        and all(part not in ("", ".", "..") and part.casefold() != ".git" for part in value.split("/")),
        "Selection path is unsafe",
    )
    return value


def safe_remote(value):
    require(isinstance(value, str) and value.strip() == value and value, "Remote must be explicit")
    if Path(value).is_absolute():
        canonical(Path(value), directory=True)
        return value
    parsed = urlsplit(value)
    require(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment,
        "Remote must be credential-free HTTPS or an explicit local bare repository",
    )
    return value


@dataclass(frozen=True)
class Change:
    path: str
    index: str
    worktree: str
    original: str | None = None


def parse_status(output: bytes):
    fields = output.decode("utf-8", errors="strict").split("\0")
    changes = []
    position = 0
    while position < len(fields) and fields[position]:
        record = fields[position]
        require(len(record) >= 4 and record[2] == " ", "Git status has an invalid record")
        index, worktree = record[:2]
        require("U" not in (index, worktree) and record[:2] not in ("AA", "DD"), "Index conflicts remain")
        name = portable(record[3:])
        position += 1
        original = None
        if index in "RC" or worktree in "RC":
            require(position < len(fields) and fields[position], "Git rename record is incomplete")
            original = portable(fields[position])
            position += 1
        changes.append(Change(name, index, worktree, original))
    require(position == len(fields) - 1 and fields[-1] == "", "Git status lacks its NUL terminator")
    require(len({change.path for change in changes}) == len(changes), "Git status repeats a source path")
    return changes


class Git:
    def __init__(self, executable: Path, *, runner=subprocess.run):
        self.executable = canonical(executable)
        self.runner = runner

    def call(self, root, *arguments, allowed=(0,), stdin=None):
        environment = dict(os.environ)
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        literal_paths = [] if arguments[0] == "check-ignore" else ["--literal-pathspecs"]
        result = self.runner(
            [
                str(self.executable),
                "--no-optional-locks",
                *literal_paths,
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                *arguments,
            ],
            cwd=root,
            env=environment,
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            check=False,
        )
        require(result.returncode in allowed, f"Git {arguments[0]} failed; inspect before continuing")
        return result

    def text(self, root, *arguments):
        return self.call(root, *arguments).stdout.decode("utf-8", errors="strict").rstrip("\n")

    def remote_head(self, repository):
        reference = f"refs/heads/{repository.branch}"
        output = self.text(repository.root, "ls-remote", "--exit-code", "--refs", "origin", reference)
        fields = output.split("\t")
        require(
            len(fields) == 2 and fields[1] == reference and REVISION.fullmatch(fields[0]),
            "Remote ref is ambiguous",
        )
        return fields[0]


@dataclass
class Repository:
    label: str
    root: Path
    remote: str
    branch: str
    head: str
    outgoing: list[str]
    paths: dict[str, str]
    message: str
    gitlinks: dict[str, str] = field(default_factory=dict)


@contextmanager
def operation_lock(path):
    if os.path.lexists(path):
        canonical(path, exclusive=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DeliveryError("Another Git delivery operation holds the lock") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Workspace:
    def __init__(self, workspace: Path, selection: dict, git: Git):
        self.root = canonical(workspace, directory=True)
        self.selection = selection
        self.git = git
        self.policy = None
        self.policy_path = None
        self.captured_baselines = None
        require(
            type(selection) is dict and set(selection) == {"repositories"}, "Malformed workspace selection"
        )
        require(
            type(selection["repositories"]) is list and selection["repositories"], "No selected repositories"
        )
        self.repositories = []
        required = {"label", "root", "remote", "branch", "head", "outgoing", "paths", "message"}
        for entry in selection["repositories"]:
            require(
                type(entry) is dict and required <= set(entry) <= required | {"gitlinks"},
                "Malformed repository selection",
            )
            require(
                isinstance(entry["label"], str) and LABEL.fullmatch(entry["label"]),
                "Repository label is invalid",
            )
            relative = portable(entry["root"], root=True)
            root = canonical(self.root if relative == "." else self.root / relative, directory=True)
            require(root.is_relative_to(self.root), "Repository escapes the workspace")
            require(
                isinstance(entry["branch"], str) and entry["branch"] and not entry["branch"].startswith("-"),
                "Branch is invalid",
            )
            require(
                isinstance(entry["head"], str) and REVISION.fullmatch(entry["head"]),
                "Selected HEAD is invalid",
            )
            require(
                type(entry["outgoing"]) is list
                and all(isinstance(value, str) and REVISION.fullmatch(value) for value in entry["outgoing"]),
                "Outgoing commit selection is invalid",
            )
            require(
                isinstance(entry["message"], str) and entry["message"].strip(), "Commit message is required"
            )
            require(type(entry["paths"]) is list, "Approved paths must be explicit")
            paths = {}
            for selected in entry["paths"]:
                require(
                    type(selected) is dict and set(selected) == {"path", "stage"},
                    "Malformed staging selection",
                )
                path = portable(selected["path"])
                require(
                    path not in paths and selected["stage"] in ("index", "worktree", "whole"),
                    "Duplicate path or invalid staging mode",
                )
                paths[path] = selected["stage"]
            links = entry.get("gitlinks", {})
            require(type(links) is dict, "Gitlink mapping is invalid")
            for path, label in links.items():
                portable(path)
                require(isinstance(label, str) and LABEL.fullmatch(label), "Gitlink target label is invalid")
                require(
                    path in paths and paths[path] == "worktree",
                    "Parent gitlinks need explicit worktree staging",
                )
            self.repositories.append(
                Repository(
                    entry["label"],
                    root,
                    safe_remote(entry["remote"]),
                    entry["branch"],
                    entry["head"],
                    entry["outgoing"],
                    paths,
                    entry["message"],
                    links,
                )
            )
        self.by_label = {repository.label: repository for repository in self.repositories}
        require(len(self.by_label) == len(self.repositories), "Repository labels must be unique")
        require(
            len({repository.root for repository in self.repositories}) == len(self.repositories),
            "Repository roots must be distinct",
        )
        for position, repository in enumerate(self.repositories):
            if repository.gitlinks:
                require(position == len(self.repositories) - 1, "Parent must be the last selected repository")
                for path, label in repository.gitlinks.items():
                    require(
                        label in self.by_label and self.by_label[label].root == repository.root / path,
                        "Parent gitlink target differs from its selected child",
                    )

    @classmethod
    def from_policy(cls, workspace, document, git, message, policy_path=None):
        require(isinstance(message, str) and message.strip(), "All-current delivery requires a message")
        require(
            type(document) is dict and set(document) == {"repositories", "protected"},
            "Malformed source-delivery policy",
        )
        require(type(document["repositories"]) is list and document["repositories"], "No policy repositories")
        selection = {"repositories": []}
        eligibility = {}
        for specification in document["repositories"]:
            required = {"label", "root", "remote", "branch"}
            require(
                type(specification) is dict
                and required <= set(specification) <= required | {"allowedPaths", "gitlinks"},
                "Malformed policy repository",
            )
            require(
                isinstance(specification["label"], str) and LABEL.fullmatch(specification["label"]),
                "Policy repository label is invalid",
            )
            require(
                isinstance(specification["branch"], str) and specification["branch"],
                "Policy branch must be explicit",
            )
            relative = portable(specification["root"], root=True)
            root = canonical(workspace if relative == "." else workspace / relative, directory=True)
            entry = {name: specification[name] for name in required}
            entry.update(
                head=git.text(root, "rev-parse", "--verify", "HEAD"),
                outgoing=[],
                paths=[],
                message=message,
            )
            if "gitlinks" in specification:
                require(type(specification["gitlinks"]) is dict, "Malformed policy gitlinks")
                entry["gitlinks"] = specification["gitlinks"]
                entry["paths"] = [{"path": path, "stage": "worktree"} for path in entry["gitlinks"]]
                require("allowedPaths" in specification, "Parent source eligibility must be explicit")
            allowed = specification.get("allowedPaths")
            require(allowed is None or type(allowed) is list, "Malformed source eligibility")
            if allowed is not None:
                for path in allowed:
                    portable(path)
                require(len(allowed) == len(set(allowed)), "Repeated source eligibility path")
            eligibility[specification["label"]] = allowed
            selection["repositories"].append(entry)
        route = cls(workspace, selection, git)
        route.policy = document
        route.policy_path = policy_path
        route.require_policy_inputs()
        baselines = {}
        for entry, repository in zip(selection["repositories"], route.repositories):
            for change in route.status(repository):
                for path in (change.path, change.original):
                    if path is None or path in repository.gitlinks:
                        continue
                    allowed = eligibility[repository.label]
                    require(allowed is None or path in allowed, "Current parent/source path is not eligible")
                    mode = (
                        "index"
                        if change.index not in (" ", "?") and change.worktree == " "
                        else "whole"
                        if change.index not in (" ", "?")
                        else "worktree"
                    )
                    repository.paths[path] = mode
            entry["paths"] = [{"path": path, "stage": mode} for path, mode in repository.paths.items()]
            route.validate_paths(repository, route.status(repository))
            baselines[repository.label] = route.snapshot(repository)
        route.captured_baselines = baselines
        return route

    def require_policy_inputs(self):
        if self.policy_path is not None:
            canonical(self.policy_path, exclusive=True)
            current = json.loads(
                self.policy_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object
            )
            require(current == self.policy, "Source policy changed from its captured selection")
        require(type(self.policy["protected"]) is list, "Malformed protected source inputs")
        guarded = {}
        for item in self.policy["protected"]:
            require(type(item) is dict and set(item) == {"root", "paths"}, "Malformed protected source")
            relative = portable(item["root"], root=True)
            root = self.root if relative == "." else self.root / relative
            require(root in {repository.root for repository in self.repositories}, "Unknown protected owner")
            require(type(item["paths"]) is list, "Malformed protected paths")
            guarded.setdefault(root, set()).update(portable(path) for path in item["paths"])
        for repository in self.repositories:
            if repository.gitlinks:
                guarded.setdefault(repository.root, set()).add(".gitmodules")
            if self.policy_path is not None and self.policy_path.is_relative_to(repository.root):
                guarded.setdefault(repository.root, set()).add(
                    self.policy_path.relative_to(repository.root).as_posix()
                )
            changed = {
                path for change in self.status(repository) for path in (change.path, change.original) if path
            }
            require(
                not changed & guarded.get(repository.root, set()),
                "Policy/registration/source authority changed; use reviewed selection",
            )

    def require_destination(self, repository):
        for arguments in (
            ("remote", "get-url", "--all", "origin"),
            ("remote", "get-url", "--push", "--all", "origin"),
        ):
            require(
                self.git.text(repository.root, *arguments) == repository.remote,
                "Git destination differs or has multiple URLs",
            )
        mirror = self.git.call(
            repository.root, "config", "--get", "--bool", "remote.origin.mirror", allowed=(0, 1)
        )
        require(
            mirror.returncode == 1
            and not mirror.stdout
            or mirror.returncode == 0
            and mirror.stdout == b"false\n",
            "Mirror Git destinations are prohibited",
        )
        require(
            self.git.text(repository.root, "symbolic-ref", "--quiet", "--short", "HEAD") == repository.branch,
            "Selected branch changed or is detached",
        )

    def require_registration(self, repository):
        configured = self.git.call(
            repository.root,
            "config",
            "--file",
            ".gitmodules",
            "-z",
            "--get-regexp",
            r"^submodule\..*\.(path|url)$",
        ).stdout.decode("utf-8")
        modules = {}
        for record in configured.split("\0"):
            if not record:
                continue
            key, value = record.split("\n", 1)
            name, kind = key.rsplit(".", 1)
            fields = modules.setdefault(name, {})
            require(kind not in fields, "Registration repeats a path or URL")
            fields[kind] = value
        require(
            all(set(fields) == {"path", "url"} for fields in modules.values()),
            "Registration requires a path and canonical URL",
        )
        registered = {fields["path"]: fields["url"] for fields in modules.values()}
        expected = {path: self.by_label[label].remote for path, label in repository.gitlinks.items()}
        require(
            len(registered) == len(modules) and registered == expected,
            "Parent selection does not match registered child paths and destinations",
        )
        tracked = self.git.call(repository.root, "ls-files", "--stage", "-z").stdout.decode("utf-8")
        modes = {
            record.split("\t", 1)[1]: record.split(" ", 1)[0] for record in tracked.split("\0") if record
        }
        require(
            all(modes.get(path) == "160000" for path in expected), "Registered child is not a tracked gitlink"
        )

    def status(self, repository):
        return parse_status(
            self.git.call(
                repository.root,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=dirty",
            ).stdout
        )

    def validate_paths(self, repository, changes):
        index_deletions = {
            change.path
            for change in changes
            if change.index == "D" and repository.paths.get(change.path) == "index"
        }
        ignored_candidates = []
        for path in repository.paths:
            if path in index_deletions:
                continue
            candidate = repository.root / path
            require(
                candidate.resolve(strict=False).is_relative_to(repository.root),
                "Approved source escapes its repository",
            )
            if os.path.lexists(candidate):
                canonical(candidate, directory=path in repository.gitlinks, exclusive=True)
            if path not in repository.gitlinks:
                ignored_candidates.append(path)
        if ignored_candidates:
            ignored = self.git.call(
                repository.root,
                "check-ignore",
                "--stdin",
                "-z",
                allowed=(0, 1),
                stdin="".join(path + "\0" for path in ignored_candidates).encode("utf-8"),
            )
            require(
                ignored.returncode == 1 and not ignored.stdout,
                "Ignored source is not eligible for Git delivery",
            )
        for change in changes:
            if change.index not in (" ", "?"):
                require(
                    change.path in repository.paths
                    and (change.original is None or change.original in repository.paths),
                    "Unapproved staged source is present",
                )
            if change.path not in repository.paths:
                continue
            require(
                change.original is None or change.original in repository.paths,
                "Both rename endpoints require approval",
            )
            if change.index not in (" ", "?") and change.worktree != " ":
                require(
                    repository.paths[change.path] in ("index", "whole"),
                    "Partially staged source needs index preservation or explicit whole-file approval",
                )

    def preflight(self, repository, recorded=None):
        root = repository.root
        expected_head = recorded.get("commit") or repository.head if recorded else repository.head
        require(
            Path(self.git.text(root, "rev-parse", "--show-toplevel")) == root,
            "Selected path is not its Git worktree root",
        )
        self.git.call(root, "check-ref-format", "--branch", repository.branch)
        require(
            self.git.text(root, "symbolic-ref", "--quiet", "--short", "HEAD") == repository.branch,
            "Selected branch changed or is detached",
        )
        require(
            self.git.text(root, "rev-parse", "--verify", "HEAD") == expected_head, "Selected HEAD drifted"
        )
        self.require_destination(repository)
        require(not self.git.call(root, "ls-files", "--unmerged", "-z").stdout, "Index conflicts remain")
        operation_names = (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        )
        paths = self.git.text(
            root,
            "rev-parse",
            "--path-format=absolute",
            *(argument for name in operation_names for argument in ("--git-path", name)),
        ).splitlines()
        require(
            len(paths) == len(operation_names) and all(Path(path).is_absolute() for path in paths),
            "Git operation paths are ambiguous",
        )
        require(not any(os.path.lexists(path) for path in paths), "Another Git operation is in progress")
        remote = self.git.remote_head(repository)
        self.git.call(root, "cat-file", "-e", f"{remote}^{{commit}}")
        self.git.call(root, "merge-base", "--is-ancestor", remote, expected_head)
        outgoing = self.git.text(root, "rev-list", "--reverse", f"{remote}..{expected_head}").splitlines()
        if recorded:
            require(remote in (recorded["remoteBefore"], expected_head), "Original remote branch drifted")
            expected_outgoing = (
                []
                if remote == expected_head
                else repository.outgoing + ([expected_head] if expected_head != repository.head else [])
            )
        else:
            expected_outgoing = repository.outgoing
        require(outgoing == expected_outgoing, "Outgoing commits differ from the approved range")
        changes = self.status(repository)
        self.validate_paths(repository, changes)
        for arguments in (
            ("diff", "--no-ext-diff", "--no-textconv", "--check"),
            ("diff", "--cached", "--no-ext-diff", "--no-textconv", "--check"),
        ):
            self.git.call(root, *arguments)
        if repository.gitlinks:
            self.require_registration(repository)
        return {
            "label": repository.label,
            "branch": repository.branch,
            "head": expected_head,
            "remoteHead": remote,
            "outgoing": outgoing,
            "message": repository.message,
            "paths": [{"path": path, "stage": mode} for path, mode in repository.paths.items()],
            "gitlinks": repository.gitlinks,
            "unselected": sorted(change.path for change in changes if change.path not in repository.paths),
        }

    def review(self, recorded=None):
        indexes = []
        for repository in self.repositories:
            path = Path(
                self.git.text(repository.root, "rev-parse", "--path-format=absolute", "--git-path", "index")
            )
            require(path.is_absolute(), "Index path is not absolute")
            if path.exists():
                canonical(path, exclusive=True)
            indexes.append(path.resolve(strict=False))
        require(len(set(indexes)) == len(indexes), "Selected worktrees share an index")
        return {
            "status": "reviewed",
            "repositories": [
                self.preflight(repository, recorded[repository.label] if recorded else None)
                for repository in self.repositories
            ],
        }

    def control_path(self):
        require(
            Path(self.git.text(self.root, "rev-parse", "--show-toplevel")) == self.root,
            "Workspace control requires its explicit Git root",
        )
        path = Path(
            self.git.text(
                self.root, "rev-parse", "--path-format=absolute", "--git-path", "git-workspace-delivery.json"
            )
        )
        require(path.is_absolute(), "Operation control path must be absolute")
        canonical(path.parent, directory=True)
        if os.path.lexists(path):
            canonical(path, exclusive=True)
        return path

    def read_control(self):
        path = self.control_path()
        if not path.exists():
            return None
        control = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        require(
            type(control) is dict
            and {"selection", "repositories"}
            <= set(control)
            <= {"selection", "repositories", "baselines", "policy"}
            and control["selection"] == self.selection,
            "Original operation selection differs; never reconstruct it",
        )
        require(
            type(control["repositories"]) is dict and set(control["repositories"]) == set(self.by_label),
            "Operation control lacks its selected repositories",
        )
        for value in control["repositories"].values():
            require(
                type(value) is dict and set(value) == {"phase", "remoteBefore", "commit"},
                "Malformed repository operation control",
            )
            require(
                value["phase"]
                in ("selected", "commit-attempted", "committed", "push-attempted", "pushed", "skipped"),
                "Unknown repository operation phase",
            )
            require(
                isinstance(value["remoteBefore"], str) and REVISION.fullmatch(value["remoteBefore"]),
                "Original remote target is invalid",
            )
            require(
                value["commit"] is None
                or isinstance(value["commit"], str)
                and REVISION.fullmatch(value["commit"]),
                "Original intended commit is invalid",
            )
        if "baselines" in control:
            require(
                type(control["baselines"]) is dict and set(control["baselines"]) == set(self.by_label),
                "Original captured state lacks selected repositories",
            )
            for baseline in control["baselines"].values():
                decode_baseline(baseline)
        if "policy" in control:
            policy = control["policy"]
            require(
                type(policy) is dict and set(policy) == {"path", "document"},
                "Original policy control is malformed",
            )
            require(
                isinstance(policy["path"], str) and Path(policy["path"]).is_absolute(),
                "Original policy path is invalid",
            )
            require(
                type(policy["document"]) is dict and set(policy["document"]) == {"repositories", "protected"},
                "Original caller policy is malformed",
            )
            if self.policy is not None:
                require(
                    policy["document"] == self.policy and policy["path"] == str(self.policy_path),
                    "Policy differs from the original captured operation",
                )
            else:
                self.policy = policy["document"]
                self.policy_path = Path(policy["path"])
        return control

    def save_control(self, control, *, create=False):
        path = self.control_path()
        source = (json.dumps(control, sort_keys=True) + "\n").encode("utf-8")
        if create:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            descriptor, name = tempfile.mkstemp(prefix=".git-delivery-", dir=path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(source)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def sources(self, repository):
        result = {}
        for path, mode in repository.paths.items():
            if mode == "index" or path in repository.gitlinks:
                continue
            candidate = repository.root / path
            result[path] = (
                canonical(candidate, exclusive=True).read_bytes() if os.path.lexists(candidate) else None
            )
        return result

    def snapshot(self, repository):
        return {
            "head": self.git.text(repository.root, "rev-parse", "HEAD"),
            "index": self.git.call(repository.root, "ls-files", "--stage", "-z").stdout,
            "sources": self.sources(repository),
        }

    def verify_commit(self, repository, commit):
        observed = self.git.text(repository.root, "show", "-s", "--format=%P%x00%B", commit)
        require("\0" in observed, "Committed source metadata is ambiguous")
        parent, message = observed.split("\0", 1)
        require(
            parent == repository.head,
            "New commit does not have its original selected parent",
        )
        require(
            message == repository.message.rstrip("\n"),
            "Commit message differs from the approved message",
        )
        changed = (
            self.git.call(
                repository.root,
                "diff-tree",
                "--no-commit-id",
                "--no-renames",
                "--name-only",
                "-r",
                "-z",
                repository.head,
                commit,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        require(
            set(changed) - {""} <= set(repository.paths),
            "Commit contains unapproved source changes; do not push",
        )

    def observed_commit(self, repository, recorded):
        head = self.git.text(repository.root, "rev-parse", "HEAD")
        if recorded["commit"]:
            require(head == recorded["commit"], "Original intended commit drifted")
            return head
        if recorded["phase"] == "commit-attempted" and head != repository.head:
            self.verify_commit(repository, head)
            return head
        require(head == repository.head, "Selected HEAD drifted outside the recorded operation")
        return None

    def captured_state(self, repository, control):
        require("baselines" in control, "Original captured source/index is missing; inspect only")
        return decode_baseline(control["baselines"][repository.label])

    def require_commit_state(self, repository, commit, control):
        if "baselines" not in control:
            return
        expected = self.captured_state(repository, control)
        expected["head"] = commit
        require(
            self.snapshot(repository) == expected, "Source/index changed after the captured commit intent"
        )

    def inspect(self):
        control = self.read_control()
        if control is None:
            return {"status": "no-recorded-operation", "repositories": self.review()["repositories"]}
        results = []
        for repository in self.repositories:
            recorded = control["repositories"][repository.label]
            commit = self.observed_commit(repository, recorded)
            if commit is not None:
                self.require_commit_state(repository, commit, control)
            remote = self.git.remote_head(repository)
            status = (
                "delivered"
                if commit and remote == commit
                else "not-pushed"
                if commit and remote == recorded["remoteBefore"]
                else "commit-not-observed"
                if commit is None and remote == recorded["remoteBefore"]
                else "remote-drifted-inspect-only"
            )
            results.append(
                {
                    "label": repository.label,
                    "phase": recorded["phase"],
                    "commit": commit,
                    "remoteHead": remote,
                    "status": status,
                }
            )
        return {"status": "inspected", "repositories": results}

    def require_children(self, repository, control):
        if not repository.gitlinks:
            return
        for selected in self.repositories[:-1]:
            recorded = control["repositories"][selected.label]
            require(
                recorded["phase"] in ("pushed", "skipped") and recorded["commit"],
                "Parent waits for every selected child/extra target",
            )
            require(
                self.git.remote_head(selected) == recorded["commit"]
                and self.git.text(selected.root, "rev-parse", "HEAD") == recorded["commit"],
                "A delivered child branch drifted before the parent",
            )

    def stage(self, repository):
        changes = self.status(repository)
        self.validate_paths(repository, changes)
        changed = {change.path for change in changes} | {
            change.original for change in changes if change.original
        }
        paths = [
            path
            for path, mode in repository.paths.items()
            if mode != "index" and (path in changed or path in repository.gitlinks)
        ]
        if paths:
            self.git.call(
                repository.root,
                "add",
                "--all",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                stdin="".join(path + "\0" for path in paths).encode("utf-8"),
            )
        staged = (
            self.git.call(
                repository.root,
                "diff",
                "--cached",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        require(
            set(staged) - {""} <= set(repository.paths),
            "Index changed outside the approved staging selection",
        )
        self.git.call(repository.root, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--check")
        return bool(set(staged) - {""})

    def deliver(self, repository, control, baseline):
        recorded = control["repositories"][repository.label]
        if self.policy is not None:
            self.require_policy_inputs()
        self.require_children(repository, control)
        self.preflight(repository, recorded)
        require(self.snapshot(repository) == baseline, "Source/index changed concurrently before staging")
        if recorded["phase"] in ("pushed", "skipped"):
            require(
                not any(change.path in repository.paths for change in self.status(repository)),
                "Delivered source changed; inspect rather than recommit",
            )
            return
        if recorded["commit"] is None:
            changed = self.stage(repository)
            require(
                self.sources(repository) == baseline["sources"], "Source changed concurrently during staging"
            )
            if changed:
                staged_state = self.snapshot(repository)
                control["baselines"][repository.label] = encode_baseline(staged_state)
                recorded["phase"] = "commit-attempted"
                self.save_control(control)
                self.git.call(repository.root, "commit", "-m", repository.message)
                commit = self.git.text(repository.root, "rev-parse", "HEAD")
                self.verify_commit(repository, commit)
                require(
                    self.sources(repository) == baseline["sources"]
                    and self.snapshot(repository)["index"] == staged_state["index"],
                    "Source/hook changed during commit; inspect before push",
                )
            else:
                commit = repository.head
            recorded.update({"phase": "committed", "commit": commit})
            control["baselines"][repository.label] = encode_baseline(self.snapshot(repository))
            self.save_control(control)
        require(
            self.git.text(repository.root, "rev-parse", "HEAD") == recorded["commit"],
            "Intended commit drifted before push",
        )
        self.require_destination(repository)
        remote = self.git.remote_head(repository)
        if remote == recorded["commit"]:
            recorded["phase"] = (
                "pushed" if recorded["commit"] != repository.head or repository.outgoing else "skipped"
            )
            self.save_control(control)
            return
        require(remote == recorded["remoteBefore"], "Remote drifted before push; inspect without replay")
        recorded["phase"] = "push-attempted"
        self.save_control(control)
        self.git.call(
            repository.root,
            "push",
            "--no-follow-tags",
            "origin",
            f"{recorded['commit']}:refs/heads/{repository.branch}",
        )
        require(
            self.git.remote_head(repository) == recorded["commit"],
            "Push is unconfirmed or remote drifted; inspect",
        )
        recorded["phase"] = "pushed"
        self.save_control(control)

    def apply(self, *, dry_run=False, continue_operation=False, lock_held=False):
        if dry_run:
            require(not continue_operation, "Dry-run must review a fresh selection")
            require(self.read_control() is None, "Original operation exists; inspect before continuation")
            result = self.review()
            result["status"] = "dry-run"
            return result
        control_file = self.control_path()
        with nullcontext() if lock_held else operation_lock(control_file.with_suffix(".lock")):
            control = self.read_control()
            require(
                (control is not None) == continue_operation,
                "Use inspect and explicit continuation for an existing operation",
            )
            if self.policy is not None:
                self.require_policy_inputs()
            if control:
                for repository in self.repositories:
                    recorded = control["repositories"][repository.label]
                    if recorded["phase"] == "commit-attempted":
                        commit = self.observed_commit(repository, recorded)
                        if commit:
                            self.require_commit_state(repository, commit, control)
                            recorded.update({"phase": "committed", "commit": commit})
            reviewed = self.review(control["repositories"] if control else None)
            baselines = (
                {
                    repository.label: self.captured_state(repository, control)
                    for repository in self.repositories
                }
                if control
                else self.captured_baselines
                or {repository.label: self.snapshot(repository) for repository in self.repositories}
            )
            if control:
                for repository in self.repositories:
                    commit = control["repositories"][repository.label]["commit"]
                    if commit is not None:
                        baselines[repository.label]["head"] = commit
            for repository in self.repositories:
                require(
                    self.snapshot(repository) == baselines[repository.label],
                    "Captured source/index changed before delivery",
                )
            if (
                control is None
                and self.policy is not None
                and all(
                    not self.status(repository) and not repository.outgoing
                    for repository in self.repositories
                )
            ):
                return {
                    "status": "nothing-to-do",
                    "repositories": [
                        {
                            "label": repository.label,
                            "status": "skipped",
                            "commit": repository.head,
                            "remaining": [],
                        }
                        for repository in self.repositories
                    ],
                }
            if control is None:
                control = {
                    "selection": self.selection,
                    "baselines": {label: encode_baseline(baseline) for label, baseline in baselines.items()},
                    "repositories": {
                        value["label"]: {
                            "phase": "selected",
                            "remoteBefore": value["remoteHead"],
                            "commit": None,
                        }
                        for value in reviewed["repositories"]
                    },
                }
                if self.policy is not None and self.policy_path is not None:
                    control["policy"] = {"path": str(self.policy_path), "document": self.policy}
                self.save_control(control, create=True)
            else:
                self.save_control(control)
            for repository in self.repositories:
                if repository.gitlinks:
                    baseline = baselines[repository.label]
                    require(
                        self.snapshot(repository) == baseline,
                        "Parent source/index changed while children were delivered",
                    )
                self.deliver(repository, control, baselines[repository.label])
            for repository in self.repositories:
                recorded = control["repositories"][repository.label]
                require(
                    self.git.remote_head(repository) == recorded["commit"]
                    and self.git.text(repository.root, "rev-parse", "HEAD") == recorded["commit"],
                    "Delivered ref drifted before completion",
                )
                if repository.gitlinks:
                    entries = self.git.call(
                        repository.root,
                        "ls-tree",
                        "-rz",
                        "--full-tree",
                        "HEAD",
                        "--",
                        *repository.gitlinks,
                    ).stdout.decode("utf-8")
                    observed = {}
                    for entry in entries.split("\0"):
                        if entry:
                            metadata, path = entry.split("\t", 1)
                            mode, kind, target = metadata.split(" ")
                            require(
                                mode == "160000" and kind == "commit", "Parent selection is not a gitlink"
                            )
                            observed[path] = target
                    expected = {
                        path: control["repositories"][label]["commit"]
                        for path, label in repository.gitlinks.items()
                    }
                    require(observed == expected, "Parent gitlink does not select the delivered child commit")
            result = {
                "status": "delivered",
                "repositories": [
                    {
                        "label": repository.label,
                        "status": control["repositories"][repository.label]["phase"],
                        "commit": control["repositories"][repository.label]["commit"],
                        "remaining": sorted(change.path for change in self.status(repository)),
                    }
                    for repository in self.repositories
                ],
            }
            control_file.unlink()
            return result


def policy_command(arguments, git):
    root = canonical(arguments.workspace, directory=True)
    require(
        Path(git.text(root, "rev-parse", "--show-toplevel")) == root, "Policy workspace is not its Git root"
    )
    policy_path = canonical(arguments.policy, exclusive=True)
    document = json.loads(policy_path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    control_file = Path(
        git.text(root, "rev-parse", "--path-format=absolute", "--git-path", "git-workspace-delivery.json")
    )
    canonical(control_file.parent, directory=True)

    def selected_route():
        if os.path.lexists(control_file):
            canonical(control_file, exclusive=True)
            original = json.loads(control_file.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            require(
                type(original) is dict and "selection" in original, "Original delivery control is malformed"
            )
            route = Workspace(root, original["selection"], git)
            route.policy = document
            route.policy_path = policy_path
            return route
        require(not arguments.continue_operation, "No original operation exists for continuation")
        return Workspace.from_policy(
            root, document, git, arguments.message or "Inspect current source", policy_path
        )

    if arguments.operation == "inspect":
        return selected_route().inspect()
    if arguments.operation == "review" or arguments.dry_run:
        require(not arguments.continue_operation, "Preview never continues a mutation")
        route = selected_route()
        return route.review() if arguments.operation == "review" else route.apply(dry_run=True)
    with operation_lock(control_file.with_suffix(".lock")):
        route = selected_route()
        return route.apply(continue_operation=arguments.continue_operation, lock_held=True)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("review", "apply", "inspect"))
    parser.add_argument("--workspace", type=Path, required=True)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--selection", type=Path)
    inputs.add_argument("--policy", type=Path)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--message")
    parser.add_argument("--confirm-all-current", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue", dest="continue_operation", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    arguments = parser.parse_args(arguments)
    try:
        if arguments.policy is not None:
            require(not arguments.confirm, "Policy delivery uses explicit all-current approval")
            if arguments.operation != "apply":
                require(
                    not (arguments.dry_run or arguments.continue_operation or arguments.confirm_all_current),
                    "Read-only operations reject mutation options",
                )
            else:
                require(
                    arguments.dry_run or arguments.confirm_all_current,
                    "Explicit --confirm-all-current is required",
                )
                require(
                    arguments.continue_operation
                    or isinstance(arguments.message, str)
                    and arguments.message.strip(),
                    "All-current delivery requires a commit message",
                )
            require(
                not arguments.continue_operation or arguments.message is None,
                "Continuation uses its original message",
            )
            result = policy_command(arguments, Git(arguments.git))
            print(json.dumps(result, sort_keys=True))
            return 0
        require(
            not arguments.confirm_all_current and arguments.message is None,
            "Reviewed selection already supplies exact paths and messages",
        )
        selected = canonical(arguments.selection, exclusive=True)
        require(
            not selected.is_relative_to(arguments.workspace.resolve()),
            "Selection must remain outside the workspace",
        )
        document = json.loads(selected.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        route = Workspace(arguments.workspace, document, Git(arguments.git))
        if arguments.operation != "apply":
            require(
                not (arguments.dry_run or arguments.continue_operation or arguments.confirm),
                "Read-only operations reject mutation options",
            )
            result = route.review() if arguments.operation == "review" else route.inspect()
        else:
            require(arguments.dry_run or arguments.confirm, "Explicit commit/push confirmation is required")
            result = route.apply(dry_run=arguments.dry_run, continue_operation=arguments.continue_operation)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DeliveryError, OSError, ValueError) as error:
        detail = (
            str(error)
            if isinstance(error, DeliveryError)
            else "Selected inputs/control could not be inspected safely"
        )
        print(f"Git delivery stopped: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
