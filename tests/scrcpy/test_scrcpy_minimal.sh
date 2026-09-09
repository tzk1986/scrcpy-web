#!/system/bin/sh
export CLASSPATH=/data/local/tmp/scrcpy-server.jar
# 使用最简单的参数测试
exec app_process / com.genymobile.scrcpy.Server 2.4
