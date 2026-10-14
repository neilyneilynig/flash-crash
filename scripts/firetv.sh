#!/usr/bin/env bash
# Build the Fire TV APK and sideload it onto a Fire TV stick over Wi-Fi.
#   On the TV: Settings > My Fire TV > Developer options > ADB debugging: ON
#   (no Developer options? Settings > My Fire TV > About > click the device name 7 times)
#   Then: scripts/firetv.sh <fire-tv-ip> [game-server-url]
set -euo pipefail
cd "$(dirname "$0")/../firetv"
TV_IP="${1:?usage: scripts/firetv.sh <fire-tv-ip> [server-url]}"
SERVER="${2:-}"
export JAVA_HOME="${JAVA_HOME:-$HOME/Android/jdk21}" ANDROID_HOME="${ANDROID_HOME:-$HOME/Android/Sdk}"
ADB="$ANDROID_HOME/platform-tools/adb"
echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew -q assembleRelease ${SERVER:+-PflashcrashServer=$SERVER}
APK=app/build/outputs/apk/release/app-release.apk
"$ADB" connect "$TV_IP:5555"
echo "Accept the 'Allow USB debugging' prompt on the TV if it appears..."
"$ADB" -s "$TV_IP:5555" wait-for-device
"$ADB" -s "$TV_IP:5555" install -r "$APK"
"$ADB" -s "$TV_IP:5555" shell am start -n dev.flashcrash.tv/.MainActivity
echo "Flash Crash is running on your Fire TV."
