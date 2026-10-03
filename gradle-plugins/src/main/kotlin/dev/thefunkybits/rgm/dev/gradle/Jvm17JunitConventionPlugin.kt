package dev.thefunkybits.rgm.dev.gradle

import org.gradle.api.JavaVersion
import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.plugins.JavaPluginExtension
import org.gradle.api.tasks.testing.Test
import org.gradle.jvm.toolchain.JavaLanguageVersion

class Jvm17JunitConventionPlugin : Plugin<Project> {
    override fun apply(target: Project) {
        target.pluginManager.withPlugin("java") {
            target.extensions.getByType(JavaPluginExtension::class.java).apply {
                toolchain.languageVersion.set(JavaLanguageVersion.of(17))
                sourceCompatibility = JavaVersion.VERSION_17
                targetCompatibility = JavaVersion.VERSION_17
            }
        }
        target.tasks.withType(Test::class.java).configureEach { useJUnitPlatform() }
    }
}
