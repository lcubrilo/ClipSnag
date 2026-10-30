plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val ciVersionCode = (System.getenv("VERSION_CODE") ?: "1").toInt()
val ciVersionName = System.getenv("VERSION_NAME") ?: "0.1.0-dev"
val keystorePath: String? = System.getenv("KEYSTORE_FILE")

android {
    namespace = "io.github.lcubrilo.clipsnag"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.lcubrilo.clipsnag"
        minSdk = 29
        targetSdk = 35
        versionCode = ciVersionCode
        versionName = ciVersionName
        ndk {
            // Python 3.13 on Android supports 64-bit ABIs only.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        create("release") {
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystorePath != null) signingConfigs.getByName("release")
                            else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("yt-dlp")
            install("certifi")
        }
        // Android components (activities, tile, service, receiver) are written in Python.
        staticProxy("clipsnag.components")
    }
}
