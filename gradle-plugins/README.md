# JVM 17 And JUnit Convention

This included build supplies `dev.thefunkybits.rgm.dev.jvm17-junit`. It reacts to the Java plugin,
sets Java toolchain/source/target 17 and configures every `Test` task for JUnit Platform, including
tasks registered later. Consumers own plugin application, Kotlin versions, dependencies, logging,
forks and task selection. No consumer version catalogue or private checkout is required.

Build with JDK 17 and the checked-in Gradle 8.11.1 wrapper. On Windows, invoke the wrapper main
with Java rather than a batch launcher. Tests use Gradle TestKit and public synthetic fixtures.
Gradle resolves Kotlin DSL and JUnit dependencies from the public Gradle Plugin Portal/Maven Central.

Consuming settings require an absolute `RGM_DEV_ROOT` pointing to the developer checkout, then
include its `gradle-plugins` directory. Convention build dependencies substitute
`dev.thefunkybits.rgm.dev:rgm-dev-gradle` with this included build; no registry publication is needed.

The repository-root LICENSE and NOTICE apply. Corresponding-source packets retain this build,
its sources/settings/build instructions and the compatible licence under `rgm-dev/`. After
extraction, select that directory with `RGM_DEV_ROOT`; the client wrapper can also launch this build.
