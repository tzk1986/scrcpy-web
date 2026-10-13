#!/system/bin/sh
# 设备端最小启动脚本：版本号由宿主注入（唯一分发点 backend/app/scrcpy/scrcpy_version.py）
# 用法：adb shell "SCRCPY_VERSION=<版本> sh /data/local/tmp/test_scrcpy_minimal.sh"
export CLASSPATH=/data/local/tmp/scrcpy-server.jar
exec app_process / com.genymobile.scrcpy.Server "$SCRCPY_VERSION"
