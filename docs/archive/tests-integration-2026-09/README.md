# 归档：tests/integration/ 早期 POC 脚本群

> 归档日期：2026-09-21。原位置：`tests/integration/`（21 个文件）。
> 仅作历史留档，**不在任何测试门禁中执行**。

## 这批文件是什么

2026-09-08 ~ 09-09 视频流打通期的真机调试脚本（POC），大量为
`print` + `if __name__ == "__main__"` 的手工验证脚本，无断言。

## 为什么归档

- 硬编码**已不可用设备** `192.168.8.22:5555` / `192.168.8.34:5555`（当前真机为 .18 / .33）
- 硬编码旧端口 `localhost:8000`（现为 `BACKEND_PORT`，默认 8765）
- 引用 scrcpy-server **v2.4** 参数（现为 v4.1 协议，命令格式已变）
- 引用 `tools\adb.exe` 旧路径
- 需手工起后端 + 真机才能运行，单改端口/设备无法使其恢复可执行

## 被什么取代

| 旧脚本用途 | 现替代物 |
|------------|----------|
| 视频 WS 抓包/帧率验证（test_video_stream / test_websocket_stream / test_ws_integration） | `tests/manual/capture_ws_frames.py`（方案 17 验证脚本，含帧数/逐秒 fps/NALU 直方图/关键帧间隔） |
| 编解码与服务端管理 | `tests/scrcpy/`、`tests/application/`、`tests/infrastructure/` 的单元/应用层测试 |
| 端到端用户场景 | `tests/e2e/`（Playwright，需真机，本地跑） |
| 冒烟测试 | 原 `test_health.py` 已迁至 `tests/unit/test_health.py`（入 CI 门禁） |

## 若要复活

需按当前协议重写而非修补：设备改为参数/环境变量注入、端口取 `BACKEND_PORT`、
scrcpy-server 参数对齐 v4.1、命令路径随配置。