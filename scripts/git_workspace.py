"""Explicit, source-only Git delivery across independent worktrees."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
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
                ignored = self.git.call(
                    repository.root, "check-ignore", "--quiet", "--", path, allowed=(0, 1)
                )
                require(ignored.returncode == 1, "Ignored source is not eligible for Git delivery")
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
        for arguments in (
            ("remote", "get-url", "--all", "origin"),
            ("remote", "get-url", "--push", "--all", "origin"),
        ):
            require(
                self.git.text(root, *arguments) == repository.remote,
                "Git destination differs or has multiple URLs",
            )
        mirror = self.git.call(root, "config", "--get", "--bool", "remote.origin.mirror", allowed=(0, 1))
        require(
            mirror.returncode == 1
            and not mirror.stdout
            or mirror.returncode == 0
            and mirror.stdout == b"false\n",
            "Mirror Git destinations are prohibited",
        )
        require(not self.git.call(root, "ls-files", "--unmerged", "-z").stdout, "Index conflicts remain")
        for name in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
        ):
            require(
                not os.path.lexists(
                    self.git.text(root, "rev-parse", "--path-format=absolute", "--git-path", name)
                ),
                "Another Git operation is in progress",
            )
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
            configured = self.git.call(
                root, "config", "--file", ".gitmodules", "-z", "--get-regexp", r"^submodule\..*\.path$"
            ).stdout.decode("utf-8")
            leaves = {entry.split("\n", 1)[1] for entry in configured.split("\0") if entry}
            require(
                leaves == set(repository.gitlinks), "Parent selection does not match registered child paths"
            )
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
            and set(control) == {"selection", "repositories"}
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
        require(
            self.git.text(repository.root, "show", "-s", "--format=%P", commit) == repository.head,
            "New commit does not have its original selected parent",
        )
        require(
            self.git.text(repository.root, "show", "-s", "--format=%B", commit)
            == repository.message.rstrip("\n"),
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

    def inspect(self):
        control = self.read_control()
        if control is None:
            return {"status": "no-recorded-operation", "repositories": self.review()["repositories"]}
        results = []
        for repository in self.repositories:
            recorded = control["repositories"][repository.label]
            commit = self.observed_commit(repository, recorded)
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
                recorded["phase"] = "commit-attempted"
                self.save_control(control)
                self.git.call(repository.root, "commit", "-m", repository.message)
                commit = self.git.text(repository.root, "rev-parse", "HEAD")
                self.verify_commit(repository, commit)
                require(
                    self.sources(repository) == baseline["sources"],
                    "Source/hook changed during commit; inspect before push",
                )
            else:
                commit = repository.head
            recorded.update({"phase": "committed", "commit": commit})
            self.save_control(control)
        require(
            self.git.text(repository.root, "rev-parse", "HEAD") == recorded["commit"],
            "Intended commit drifted before push",
        )
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

    def apply(self, *, dry_run=False, continue_operation=False):
        if dry_run:
            require(not continue_operation, "Dry-run must review a fresh selection")
            require(self.read_control() is None, "Original operation exists; inspect before continuation")
            result = self.review()
            result["status"] = "dry-run"
            return result
        control_file = self.control_path()
        with operation_lock(control_file.with_suffix(".lock")):
            control = self.read_control()
            require(
                (control is not None) == continue_operation,
                "Use inspect and explicit continuation for an existing operation",
            )
            if control:
                for repository in self.repositories:
                    recorded = control["repositories"][repository.label]
                    if recorded["phase"] == "commit-attempted":
                        commit = self.observed_commit(repository, recorded)
                        if commit:
                            recorded.update({"phase": "committed", "commit": commit})
            reviewed = self.review(control["repositories"] if control else None)
            baselines = {repository.label: self.snapshot(repository) for repository in self.repositories}
            if control is None:
                control = {
                    "selection": self.selection,
                    "repositories": {
                        value["label"]: {
                            "phase": "selected",
                            "remoteBefore": value["remoteHead"],
                            "commit": None,
                        }
                        for value in reviewed["repositories"]
                    },
                }
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
                for path, label in repository.gitlinks.items():
                    target = self.git.text(repository.root, "rev-parse", f"HEAD:{path}")
                    require(
                        target == control["repositories"][label]["commit"],
                        "Parent gitlink does not select the delivered child commit",
                    )
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("review", "apply", "inspect"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue", dest="continue_operation", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    arguments = parser.parse_args()
    try:
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
