# OpenScrcpy Windows 绿色版发布说明

## 系统要求

- Windows 10/11 x64
- Chrome 浏览器（最新两个稳定版）：视频解码依赖 WebCodecs，仅支持 Chrome
- 除 Chrome 外**零安装**：adb、scrcpy-server、Python 运行时全部随包分发

## 安装与启动

1. 解压 `OpenScrcpy-win64-<版本>.zip` 到任意**可写**目录（建议非 OneDrive/网络盘；中文路径未验证）
2. 双击 `OpenScrcpy.exe`，服务启动后自动打开浏览器（默认 http://127.0.0.1:8765）
3. 首次运行会在 exe 同级创建 `data/`（SQLite 库）与 `logs/`（运行日志）

**头部无窗口属正常现象**：本包以 `--noconsole` 模式运行，日志落入
`logs/openscrcpy.log` 而非控制台。启动期（Python 拦截显隐窗口之前）出错时
无任何输出，此时用 [DebugView](https://learn.microsoft.com/sysinternals/downloads/debugview)
（Sysinternals）抓取 bootloader 的调试输出。

## 端口与单实例

- 默认绑定 127.0.0.1:8765（仅本机访问：一方面避免 Windows 防火墙弹窗，
  另一方面非 HTTPS 的局域网 http 不具备安全上下文、WebCodecs 不可用，
  视频会退化为截图模式——局域网使用请自行承担该后果）
- 8765 被占时自 8765 起依次尝试共 5 个端口（8766…最多到 8769），实际端口记录在 `data/port.txt`
- 重复双击不会起第二实例：自动打开浏览器指向已运行实例

## adb 端口冲突（5037）

本包自带 adb 并默认使用标准 adb server 端口 5037。若本机已有 Android
Studio 等其他 adb 环境且其 server 版本与本包不一致，adb 客户端会**杀掉旧
server** 重新拉起（adb 官方行为）。隔离方案：为绿色版单独设置环境变量后启动

```powershell
$env:ANDROID_ADB_SERVER_PORT = 5039
.\OpenScrcpy.exe
```

## 数据位置与升级

- 用户数据（`data/`、`config/`、`logs/`）位于 exe 同级；exe 同级不可写
  （如装在 Program Files）时回落 `%LOCALAPPDATA%\OpenScrcpy`
- 配置文件改成 exe 同级的 `config/base.yaml` 后 2 秒内热重载生效（无需重启；端口、数据路径等仅启动时读取的配置除外）
- **升级**：解压新版并覆盖 `OpenScrcpy.exe` 与 `_internal/` 时，**保留**
  `data/` 与 `config/`（用户数据永不写入 `_internal`，覆盖即升级、数据不丢）

## 杀软误报

PyInstaller 产物可能被 Defender/360/火绒等启发式误报。本包已关闭 UPX
（UPX 压缩是常见误报特征）。若触发误报，添加信任或改用压缩包内解压
运行；代码签名不在当前范围内（§8.6-5 未实测）。