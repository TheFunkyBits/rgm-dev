# RGM Developer Tooling Agent Guidance

This public guide applies to standalone checkouts; no private parent or inputs are required.
Read [README.md](README.md) before CLI/check/delivery work and
[gradle-plugins/README.md](gradle-plugins/README.md) before JVM plugin work. Links do not load instructions.

## Ownership

Own generic developer CLIs, hosted style glue, and the JVM17/JUnit convention plugin.
Consumers own target style/hygiene policies, dependencies, runtime, Profile/content, native/Core,
release, private authoring, device, and retention contracts. Use explicit CLI/plugin boundaries,
not copied engines or cross-repository imported utilities. Keep implementations and synthetic tests here.

## Public Source And Licensing

Keep GPL-2.0-or-later obligations and original licence/source custody. Owner-approved private inputs
remain with their owners; never copy Title data, private fixtures/allowlists, raw installations,
player/operational saves/captures, vault contents, credentials, machine/device/account state, or
operation records into public source, logs, CI, or artifacts. Never upload private material to third
parties or pass secrets through the model. Failure artifacts may contain text, memory values, and
coordinates, not framebuffer images. No vault-capable public runners or unapproved publication.

## Execution

Use Python 3.11+ standard-library orchestration and JDK 17 with the owning checked-in wrapper.
On Windows invoke `org.gradle.wrapper.GradleWrapperMain` directly, never batch commands in persistent
terminals. Validate executable paths and pass argument arrays without shell interpretation. Run
one-shots synchronously without timeout, serialize Gradle, and track background work through exit;
no polling, interactive elevation, tool acquisition, or host installation during checks. Keep commands
on one physical line and complex PowerShell in temporary scripts.

## Tool Contracts

Require explicit target roots independently of tool checkout/cache. Confine resolved paths after
symlink/reparse resolution; reject escapes, ambiguous traversal, and shared source hard links.
Checks/preflight are source-read-only: no install, delete, or fix. Setup is explicit. Mutating tools
stage beside destinations, replace atomically, and use journalled multi-file rollback; refuse concurrent
edits and unresolved replay. Preserve the owner's stdout/stderr interface and `/` serialized paths.

Keep style and hygiene policies separate. Preserve semantic values, encoding, meaningful whitespace,
and one final logical newline on eligible text; protected originals and contractual empty markers
keep their exclusions. Follow local Git configuration/file exceptions and inspect with `git ls-files
--eol`, `git check-attr`, and `git diff --check`, not manual conversions or raw-byte EOL/EOF gates.

## Verification Timing

Finish all authorized edits across affected repositories before any build/test, including configuration,
compile-only, filtered, or smoke checks; do not split/relabel work to bypass this. During editing use
reads, source analysis, editor diagnostics, and normal diff review. Then run the smallest required
checks and reuse valid unaffected evidence. Retain failures, finish corrective edits, and rerun only
invalidated checks and dependency gates. Guidance-only work uses content/link/diff review; test-only
requests with no planned edits may run directly. Required checks are not waived. Disclose an unavoidable
higher-priority early gate's exact conflict and why read-only/editor diagnostics cannot satisfy it.

## Verification And Change Scope

For executable-contract changes, start with focused positive/negative synthetic tests. Claim actual
Windows/Linux passes only after observation; keep unrun/failed/waived/passed outcomes distinct.
Do not run broad builds/benchmarks/live operations for reassurance. The latest request controls scope;
implementation includes directly necessary code/tests/docs, but guidance/comment-only work stays in
those artifacts. Ask before crossing repositories or expanding scope. Preserve current consumers,
safeguards, useful facts, and user edits; remove directly superseded material/references, not unrelated
retirement hunts. Use Git history rather than tombstones, archives, or preservation receipts.

## Git Source Delivery

Read [Git Workspace Delivery](README.md#git-workspace-delivery) before delivery work. Keep topology,
destinations, eligibility, and authority private and caller-owned; no project discovery or release
actions. Preserve reviewed-selection/all-current semantics, protected-authority selection, all-target
preflight, child-before-parent ordering, and original captured scope. Do not add manual-selection,
separate dry-run, or post-success-inspection gates to the routine route. Dry-run/inspect are read-only
and never fetch; uncertain pushes stop for inspection and explicit continuation, not regenerated
scope, force/reset, blind replay, or history rewriting. Private controls retain their owning lifecycle.

Repository/Git/registration/CI changes, publication, signing, upload, device mutation, promotion, and
destructive/external cleanup need their applicable distinct approvals; verification grants none.
Preserve protected refs and independent original legal/source/offer/observation custody. This repo
is not an app snapshot-tag member; tags do not fulfil source offers. No identity/attestation gates.
