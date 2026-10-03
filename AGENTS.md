# RGM Developer Tooling Agent Guidance

This guide applies to standalone checkouts of the public rgm-dev repository. No private
aggregation parent or private repository is required to build or test its generic tools.

## Ownership

Own shared developer CLI tools, hosted style-CI glue and the JVM 17/JUnit convention plugin.
Consumer repositories retain runtime, Profile/content, native/Core, release, publication,
private authoring, device, target configuration and retention policies. Consumers invoke
CLI tools or the owned Gradle plugin; do not create cross-repository imported utility libraries.

## Public Source And Licensing

Keep GPL-2.0-or-later obligations and independently required original licence/source custody.
Use only generic public source and synthetic tests. Never copy Title data, private fixtures,
allowlists, vault contents, credentials, device/account state, machine paths or operational
records into this repository, logs, CI caches or artifacts. Do not change another repository's
visibility or publish source without its applicable approval.

## Execution

Use Python 3.11+ standard-library orchestration and JDK 17 with the checked-in Gradle wrapper.
On Windows invoke org.gradle.wrapper.GradleWrapperMain directly, never .bat, .cmd or cmd.exe.
Pass validated executable paths and separate argument-array elements without shell interpretation.
Run one-shot commands synchronously without a timeout; serialize Gradle. Do not poll or overlap
background builds, elevate interactively, install a Linux host or acquire tools during checking.

## Tool Contracts

Require explicit target roots independently of tool checkout and formatter cache. Confine
resolved paths after symlink/reparse resolution; reject escapes, ambiguous traversal and shared
source hard links. Checking/preflight is source-read-only and never installs, deletes or fixes.
Setup is explicit. Apply stages beside destinations, replaces atomically and uses journalled
multi-file rollback; refuse concurrent edits and unresolved transaction replay.

Preserve target-owned style and hygiene policies separately. Preserve semantic values, encoding,
meaningful whitespace and exactly one final logical newline on eligible text; protected originals
and contractual empty markers retain their owning exclusions. Follow local Git core.autocrlf
and required file-specific exceptions. No manual EOL converters or raw-byte EOL/EOF gates.

## Verification And Change Scope

Start with focused positive/negative synthetic tests for the changed contract. Prove actual
Windows/Linux outcomes before claiming them passed; unrun, failed and waived results remain
distinct. Avoid broad builds, benchmarks, native/live/device/capture/release runs for reassurance.
Preserve user edits. Remove the complete superseded dependency slice only within approved scope;
no tombstones, compatibility aliases or historical archives for unused internal work.

Repository creation, Git initialization/ref changes, commits/pushes, CI variables, source
publication and parent submodule registration require their applicable separate approvals.
This repo is not an app snapshot-tag member. It never authorizes signing, uploads, publication,
device mutation, fixture promotion or external deletion. No artifact-identity or attestation gates.
