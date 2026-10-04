# RGM Dev

Public shared developer tooling for `TheFunkyBits/rgm-dev`. Consumers
retain their configuration and invoke CLI tools or the included Gradle plugin. The private
aggregation parent is optional. Runtime, private operator and release contracts retain their
owners; this repository is not an app snapshot-tag member.

## Style And EOF

Use Python 3.11+. Provision the pinned dev-only cache explicitly on first use, then pass the
actual target Git root independently of this checkout:

```text
python -B scripts/style_setup.py --tool-root <absolute-tool-cache>
python -B scripts/style.py inventory --root <absolute-target-root>
python -B scripts/style.py check --root <absolute-target-root> --tool-root <absolute-tool-cache>
python -B scripts/style.py apply --root <absolute-target-root> --tool-root <absolute-tool-cache>
```

The target owns `config/style.json` version 1 (`protectedPaths`, `binaryExtensions`) and its
formatter configurations. Check is offline and source-read-only. Apply stages confined changes,
refuses concurrent edits/unresolved transactions and journals rollback. Retention exclusions,
encoding/BOM, LF/CRLF and significant whitespace are preserved; eligible text has one final
logical newline. Kotlin needs JDK 17 and ktfmt; ktlint is not used. Detekt stays with consumers.
`RGM_STYLE_TOOLS` selects the cache. Do not reinstall a qualified cache merely for freshness.

## Repository Hygiene

```text
python -B scripts/repository_files.py check --root <absolute-target-root> --policy <target-policy>
```

The target's `config/repository-files.json` version 1 selects `bannedExtensions`,
`skipDirectoryNames` and optional `exactAllowlistFile`. This full filesystem scan includes ignored,
untracked and production inputs under hygiene policy, not style exclusions. Allowlist membership
is exact and case-sensitive; malformed, duplicate, stale, non-banned and unsafe entries fail closed.
JSON results go to stdout, diagnostics to stderr; exits are 0 clean, 1 offenders and 2 refused input.
There is no remediation, Git/JDK/cache or network prerequisite. Private allowlists never move here.

## JDK And Wrapper

```text
python -B scripts/preflight.py --contract 1 --java-lookup java-home --project-root <project> --wrapper gradle/wrapper/gradle-wrapper.jar
```

Java lookup modes are `explicit`, `java-home`, `java-home-or-path` and `path`. Explicit mode
requires `--java`; an invalid configured Java home never falls back. Executables use repeated
`--executable NAME=PATH` or `--path-executable NAME`. The fixed Java identity probe requires JDK 17.
Only a successful versioned JSON result is accepted. No Gradle task, installation, device or
vault discovery occurs; callers construct and execute their own argument arrays.

## Gradle Convention

[gradle-plugins/README.md](gradle-plugins/README.md) describes
`dev.thefunkybits.rgm.dev.jvm17-junit`: Java toolchain/source/target 17 and JUnit Platform,
including late Test tasks. Consuming settings require absolute `RGM_DEV_ROOT` and use the
included build; convention dependencies substitute `dev.thefunkybits.rgm.dev:rgm-dev-gradle`.
Kotlin application, dependencies, logging, forks and task policy remain local. Client source
packets retain usable plugin source/licence/build inputs under `rgm-dev/`, not unrelated tools.

## Git Workspace Delivery

[scripts/git_workspace.py](scripts/git_workspace.py) delivers approved source changes across independent
worktrees. Use Python 3.11+, an absolute Git executable and an explicit workspace Git root. A caller-owned
trusted policy supports one-call all-current delivery without a manually authored selection:

```text
python -B scripts/git_workspace.py apply --workspace <root> --policy <caller-policy.json> --git <absolute-git> --message "Update source" --confirm-all-current
```

The strict policy contains `repositories` and `protected`. Repository records declare `label`, relative
`root`, credential-free canonical `remote` and `branch`; a final parent adds `gitlinks` and explicit
`allowedPaths`. Protected records declare a selected `root` and exact `paths`. Topology, destinations
and eligibility are private/caller-owned; this generic tool does not discover a project layout.
Initial policy/launcher installation and protected authority updates use reviewed selection. All-current
approval captures exact nonignored paths and HEADs internally, preserves staged-only content, and explicitly
approves whole-current content for partial staging. It never absorbs later paths or unapproved outgoing
commits. A safe no-op creates no empty commits, pushes or transaction control. Normal success reports
verified current commits directly; separate review, dry-run and follow-up inspection are not prerequisites.

The alternative reviewed-selection route keeps its per-operation JSON outside the workspace and Git:

```text
python -B scripts/git_workspace.py review --workspace <root> --selection <external-json> --git <git-executable>
python -B scripts/git_workspace.py apply --dry-run --workspace <root> --selection <external-json> --git <git-executable>
python -B scripts/git_workspace.py apply --confirm --workspace <root> --selection <external-json> --git <git-executable>
python -B scripts/git_workspace.py inspect --workspace <root> --selection <external-json> --git <git-executable>
python -B scripts/git_workspace.py apply --continue --confirm --workspace <root> --selection <external-json> --git <git-executable>
```

The selection has exactly a `repositories` array in delivery order. Each entry supplies `label`,
workspace-relative `root` (`.` for the parent), credential-free canonical `remote`, `branch`,
full reviewed `head`, approved oldest-first `outgoing` commit IDs, exact `paths` and `message`.
Each path entry is `{"path":"relative/file","stage":"worktree"}`. `index` preserves already-staged
content and any separate unstaged edits; `whole` explicitly approves replacing a partially staged
path with its whole worktree file. Both rename endpoints need approval. No directory-wide,
ignored-file or blanket staging is supported; an approved index-only deletion never reads the
retained worktree path.

The final parent entry additionally names every registered child in `gitlinks`, mapping each
parent-relative child leaf to its earlier selected label. Include unchanged children with empty
`paths` and `outgoing` arrays; they are skipped without empty commits. A one-time extra repository
may be explicitly selected before the parent without becoming a registered child. The parent
waits for all selected targets, then stages only approved gitlinks selecting their observed pushed
commits. Repository topology, path selections and messages remain caller-owned, never hardcoded here.

All targets are preflighted before Git source/index/ref mutation. Refuse unexpected staged paths,
conflicts, detached/drifting branches, shared indexes, unsafe links, noncanonical/multiple push
destinations and unknown remote ancestry. Review uses live remote refs, not stale tracking counts.
There is no implicit fetch, pull, rebase, reset, force push, tag push or deployment dispatch.
Normal Git line-ending handling remains in force. Dry-run is read-only and cannot prove hook
success or future remote acceptance.

Reviewed apply needs explicit commit/push authorization and `--confirm`; policy apply uses
`--confirm-all-current`. Pushes select the original commit
with a non-force branch refspec and `--no-follow-tags`. Stop on rejection, changed scope or lost
acknowledgement; retain successful partial work and local commits. Original selection and minimal
commit/push intent and captured source/index state live privately in the workspace Git directory as `git-workspace-delivery.json`,
with a process lock alongside it. Do not delete or alter this control to force a restart. Inspect
first, resolve the reported condition, then explicitly continue with the unchanged selection.
Policy recovery uses `inspect --policy <original-policy>` and, after inspection, `apply --continue
--confirm-all-current --policy <original-policy>`, with no replacement message. Original selection and
captured source/index state are retained; controls without sufficient captured state are inspect-only
for any remaining commit creation. An already-observed original push is accepted without replay.
Completion removes this operation's
control, not independent records, tags or history. JSON outcomes go to stdout; sanitized failures
go to stderr. Hooks and push-triggered source CI retain their owning behavior and approvals.

Focused tests: `python -B -m unittest discover -s scripts/tests -p test_git_workspace.py`.
They use only synthetic worktrees and local bare remotes; host qualification requires actual execution.

## Hosted Checks

[actions/style/action.yml](actions/style/action.yml) accepts only `target-root`, `tool-cache`,
target-relative `smoke-root` and strict `kotlin` (`true`/`false`). It derives its owner root from
the action path, validates distinct confined roots, explicitly provisions tools, runs target
`test_style_routing.py` smokes and performs a complete read-only check. No apply/upload step exists.

Consumer jobs own events, permissions, environments, timeouts and matrices. They must separately
check out target at `target/` and public dev at `rgm-dev/`, select one reviewed full SHA through
`RGM_DEV_REVISION`, disable persisted checkout credentials and use `./rgm-dev/actions/style`.
No moving ref, private-parent checkout, private token or `pull_request_target` is needed.

Repository creation, commits/pushes, CI ref configuration and parent registration require their
applicable approvals. Owner CI is configured for generic Python and Gradle TestKit contracts on
Windows and Linux; configured jobs and published source are not evidence of a hosted pass.
Hosted qualification has not been executed for this rollout. Consumer pins select reviewed source,
not test results or artifact authenticity.

See [AGENTS.md](AGENTS.md) for ownership and verification boundaries and [NOTICE.md](NOTICE.md)
for compatible source licensing. Tests use only generic public/synthetic inputs.
