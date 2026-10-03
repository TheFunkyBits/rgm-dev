plugins { `kotlin-dsl` }

group = "dev.thefunkybits.rgm.dev"

version = "0.1.0"

java { toolchain { languageVersion.set(JavaLanguageVersion.of(17)) } }

dependencies {
    testImplementation(gradleTestKit())
    testImplementation(kotlin("test"))
    testImplementation("org.junit.jupiter:junit-jupiter-api:5.11.3")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine:5.11.3")
}

tasks.withType<Test>().configureEach { useJUnitPlatform() }

gradlePlugin {
    plugins {
        register("jvm17Junit") {
            id = "dev.thefunkybits.rgm.dev.jvm17-junit"
            implementationClass = "dev.thefunkybits.rgm.dev.gradle.Jvm17JunitConventionPlugin"
        }
    }
}
