#!/usr/bin/env bash
# Smoke test on an emulator: install, open UI, share a link (picker), and run
# real downloads through the debug-only self-test hook. Artifacts go to ci-out/.
set -u
APK="$1"
PKG=io.github.lcubrilo.clipsnag
C=clipsnag.components
OUT=ci-out
mkdir -p "$OUT"
shot() { adb exec-out screencap -p > "$OUT/$1.png"; }

adb install -r -g "$APK" || exit 1
adb logcat -b main -c
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0

echo "== main activity"
adb shell am start -W -n "$PKG/$C.MainActivity"
sleep 12; shot 1_main

echo "== share intent -> picker"
adb shell "am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT 'https://www.youtube.com/watch?v=jNQXAC9IVRw' -n $PKG/$C.GrabActivity"
sleep 30; shot 2_picker
adb shell input keyevent KEYCODE_BACK; sleep 2

echo "== quick settings tile (empty clipboard -> toast, must not crash)"
adb shell cmd statusbar add-tile "$PKG/$C.LinkTileService" || true
adb shell cmd statusbar expand-settings; sleep 3; shot 3_qs
adb shell cmd statusbar click-tile "$PKG/$C.LinkTileService" || true
sleep 4; shot 4_after_tile
adb shell cmd statusbar collapse

RESULTS="$OUT/results.txt"; : > "$RESULTS"
for URL in \
  "https://www.youtube.com/watch?v=jNQXAC9IVRw" \
  "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/360/Big_Buck_Bunny_360_10s_1MB.mp4" \
  "https://www.instagram.com/reel/DfHixwjK8Bx/" ; do
  echo "== selftest $URL"
  adb logcat -b main -c
  adb shell "am start -n $PKG/$C.MainActivity --es selftest_url '$URL'"
  status=TIMEOUT
  for i in $(seq 1 90); do
    sleep 2
    L=$(adb logcat -d | grep -E "CLIPSNAG_(DONE|ERROR|SELFTEST failed)" | tail -1)
    if [ -n "$L" ]; then status="$L"; break; fi
  done
  adb logcat -d | grep -E "CLIPSNAG|python|AndroidRuntime" > "$OUT/log_$(echo "$URL" | md5sum | cut -c1-6).txt"
  echo "$URL => $status" | tee -a "$RESULTS"
done

adb shell cmd statusbar expand-notifications; sleep 3; shot 5_notifications
adb shell cmd statusbar collapse
adb shell ls -la /sdcard/Download/ClipSnag | tee "$OUT/files.txt"
adb logcat -d > "$OUT/logcat_tail.txt"
adb shell dumpsys notification --noredact | grep -A3 "$PKG" | head -80 > "$OUT/notifications.txt"
if adb logcat -d -b crash | grep -q "$PKG"; then
  echo "CRASH DETECTED"; adb logcat -d -b crash > "$OUT/crash.txt"; exit 1
fi
exit 0
