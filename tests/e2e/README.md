# 浏览器端到端测试（tests/e2e）

## 前置

1. 仓库根：`npm install && npx playwright install chromium`（仅首次）
2. ADB 设备已连接并在线：`tools/adb.exe devices`（无设备时设备类用例自动 skip）
3. 无需手动起服务：playwright webServer 自动拉起后端 8765 与前端 dev 8080（已存在实例会复用）

## 运行

- 全量：`npm run test:e2e`（仓库根）
- 单文件：`npx playwright test video-stream --config tests/e2e/playwright.config.ts`
- 有头调试：追加 `--headed --debug`
- 报告：`tests/e2e/report/index.html`（HTML reporter 产物）
- 多设备环境钉住测试设备：`E2E_DEVICE=192.168.8.34:5555 npm run test:e2e`
  （默认取设备列表第一个 online 设备；指定序列号不在列表时直接报错）

## 设计约定

- workers=1 串行（单设备 + scrcpy-server 每设备单实例）
- 设备断言统一走 debug session shell（dumpsys/settings/am），不直接依赖 adb CLI
- 视频用例同时接受 h264 与 screenshot 模式（RK3288 编码器已知问题）
- 端口硬编码 8765/8080：与 run_server.py、vite.config.ts 一致，端口可配置化落地后需同步本文件与 config
- 断言基于 `mCurrentFocus` 窗口名，不同 ROM 通知栏焦点名不同（StatusBar / Notification*），
  统一用 `/StatusBar|Notification/` 匹配
- 具有 kiosk 保活行为的应用（如被测应用会自动回前台）不适合做输入焦点断言，
  应选无此类干扰的设备跑设备类用例

## 已知设备限制（实测记录）

- 192.168.8.34（rk3288，Android 7.1.2）：忽略 `settings put system user_rotation`
  （可写入可回读但 `SurfaceOrientation` 不变）→ coordinate-mapping 用例自动 skip
- 192.168.8.33（rk3566_r，Android 11）：cooker 应用 kiosk 保活，HOME 后约 6s 自动回前台，
  输入断言不可靠，勿作为 E2E 测试设备
