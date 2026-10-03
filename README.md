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
