package dev.thefunkybits.rgm.dev.gradle

import java.io.File
import java.nio.file.Path
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue
import org.gradle.testkit.runner.GradleRunner
import org.gradle.testkit.runner.TaskOutcome
import org.junit.jupiter.api.io.TempDir

class Jvm17JunitConventionPluginTest {
    @TempDir lateinit var root: Path

    @Test
    fun `java fixtures configure late tests and reuse configuration cache`() {
        val fixture = fixture("java fixture", "id 'java'")
        val first = runner(fixture, "test", "lateTest").build()
        assertEquals(TaskOutcome.SUCCESS, first.task(":test")?.outcome)
        assertEquals(TaskOutcome.SUCCESS, first.task(":lateTest")?.outcome)
        assertExecutedTest(fixture, "test")
        assertExecutedTest(fixture, "lateTest")

        val second = runner(fixture, "test", "lateTest").build()
        assertTrue("Reusing configuration cache" in second.output, second.output)
    }

    @Test
    fun `kotlin fixtures need no consumer version catalogue`() {
        val fixture = fixture("kotlin fixture", "id 'org.jetbrains.kotlin.jvm' version '2.2.21'")
        File(fixture, "src/main/kotlin/Fixture.kt").apply {
            parentFile.mkdirs()
            writeText("fun fixtureValue(): String = \"fixture\"\n")
        }
        val result = runner(fixture, "test", "lateTest").build()
        assertEquals(TaskOutcome.SUCCESS, result.task(":compileKotlin")?.outcome)
        assertExecutedTest(fixture, "lateTest")
    }

    @Test
    fun `gradle plugin fixtures retain their own plugin registration`() {
        val fixture = fixture("plugin fixture", "id 'java-gradle-plugin'")
        File(fixture, "build.gradle")
            .appendText(
                "\n" +
                    """
                    gradlePlugin {
                        plugins {
                            fixture {
                                id = 'synthetic.fixture'
                                implementationClass = 'FixturePlugin'
                            }
                        }
                    }
                    """
                        .trimIndent()
            )
        File(fixture, "src/main/java/FixturePlugin.java").apply {
            parentFile.mkdirs()
            writeText(
                """
                import org.gradle.api.Plugin;
                import org.gradle.api.Project;
                public class FixturePlugin implements Plugin<Project> {
                    public void apply(Project project) {}
                }
                """
                    .trimIndent()
            )
        }
        runner(fixture, "test", "lateTest").build()
        assertTrue(
            File(fixture, "build/resources/main/META-INF/gradle-plugins/synthetic.fixture.properties").isFile
        )
        assertExecutedTest(fixture, "lateTest")
    }

    @Test
    fun `shared convention does not apply java or add dependencies`() {
        val fixture = root.resolve("no java fixture").toFile().apply { mkdirs() }
        File(fixture, "settings.gradle").writeText("rootProject.name = 'synthetic-no-java'")
        File(fixture, "build.gradle")
            .writeText(
                """
                plugins { id 'dev.thefunkybits.rgm.dev.jvm17-junit' }
                assert !plugins.hasPlugin('java')
                assert !plugins.hasPlugin('java-library')
                assert !plugins.hasPlugin('org.jetbrains.kotlin.jvm')
                def late = tasks.register('lateTest', Test) {
                    testClassesDirs = files()
                    classpath = files()
                }
                assert late.get().options instanceof org.gradle.api.tasks.testing.junitplatform.JUnitPlatformOptions
                assert configurations.isEmpty()
                """
                    .trimIndent()
            )
        runner(fixture, "lateTest").build()
    }

    private fun fixture(name: String, consumerPlugin: String): File {
        val fixture = root.resolve(name).toFile().apply { mkdirs() }
        File(fixture, "settings.gradle")
            .writeText(
                """
                pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
                rootProject.name = 'synthetic-fixture'
                """
                    .trimIndent()
            )
        File(fixture, "build.gradle")
            .writeText(
                """
            plugins {
                id 'dev.thefunkybits.rgm.dev.jvm17-junit'
                $consumerPlugin
            }
            repositories { mavenCentral() }
            dependencies {
                testImplementation 'org.junit.jupiter:junit-jupiter-api:5.11.3'
                testRuntimeOnly 'org.junit.jupiter:junit-jupiter-engine:5.11.3'
            }
            assert java.toolchain.languageVersion.get().asInt() == 17
            assert java.sourceCompatibility == JavaVersion.VERSION_17
            assert java.targetCompatibility == JavaVersion.VERSION_17
            tasks.withType(Test).configureEach {
                assert options instanceof org.gradle.api.tasks.testing.junitplatform.JUnitPlatformOptions
            }
            tasks.register('lateTest', Test) {
                testClassesDirs = sourceSets.test.output.classesDirs
                classpath = sourceSets.test.runtimeClasspath
            }
            """
                    .trimIndent()
            )
        File(fixture, "src/test/java/FixtureTest.java").apply {
            parentFile.mkdirs()
            writeText(
                """
                import org.junit.jupiter.api.Test;
                import static org.junit.jupiter.api.Assertions.assertEquals;
                public class FixtureTest {
                    @Test void usesSelectedJdk() {
                        assertEquals(17, Runtime.version().feature());
                    }
                }
                """
                    .trimIndent()
            )
        }
        return fixture
    }

    private fun assertExecutedTest(fixture: File, task: String) {
        val report = File(fixture, "build/test-results/$task/TEST-FixtureTest.xml")
        assertTrue(report.isFile, "JUnit test result is missing for $task")
        assertTrue("tests=\"1\"" in report.readText(), "JUnit fixture did not execute for $task")
    }

    private fun runner(fixture: File, vararg tasks: String): GradleRunner =
        GradleRunner.create()
            .withProjectDir(fixture)
            .withPluginClasspath()
            .withArguments(
                *tasks,
                "--configuration-cache",
                "--configuration-cache-problems=fail",
                "--stacktrace",
            )
}
