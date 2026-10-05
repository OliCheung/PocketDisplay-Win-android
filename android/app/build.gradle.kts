plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.seconddisplay.receiver"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.seconddisplay.receiver"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    signingConfigs {
        create("release") {
            // Values are only filled when a keystore is provided via environment
            // variables (e.g. in CI / a local release build). When absent the
            // config is simply unused, so a plain (unsigned) release APK still
            // builds for local testing.
            System.getenv("KEYSTORE_FILE")?.let { storeFile = file(it) }
            System.getenv("KEYSTORE_PASSWORD")?.let { storePassword = it }
            System.getenv("KEY_ALIAS")?.let { keyAlias = it }
            System.getenv("KEY_PASSWORD")?.let { keyPassword = it }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (System.getenv("KEYSTORE_FILE") != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.10.1")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.6.1")
    implementation("com.google.android.material:material:1.9.0")
}
