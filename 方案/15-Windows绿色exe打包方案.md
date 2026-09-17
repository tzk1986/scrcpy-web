# 15 - Windows 绿色 exe 打包方案（调研存档）

> **状态：暂缓执行**
> **决策（2026-09-17）**：当前阶段优先完成现有版本的功能实现与体验优化；待功能与体验达标后，以本文档为基础进行**二次评估**再决定是否实施打包。
> 性质：可行性调研（Spike），未写任何实现代码。

## 一、结论

**可行，改造量小。** 推荐 **方案A：PyInstaller onedir + 前端静态同域托管 + 自动开浏览器**，预估 **5~7 人日**。产物为绿色文件夹（zip 分发，解压双击 `OpenScrcpy.exe` 即用），保留现有全部功能。

## 二、现有架构对打包的友好度（实测）

| 依赖 | 现状 | 打包影响 |
|------|------|----------|
| ADB | 已自带 `tools/adb.exe`（6.6MB），`config/settings.py` 优先检测项目 tools 目录 | 只需将路径解析改为"冻结感知"（PyInstaller 下 `__file__` 指向 bundle 内部） |
| scrcpy | **不依赖 scrcpy.exe**，仅用 `backend/app/scrcpy/scrcpy-server.jar`（0.7MB）直连协议 | 无需打包 scrcpy 发行版；`StreamConfig.scrcpy_path` 为死配置可清理 |
| 前端 | HTTP 全部相对路径 `/api`；WS 全部基于 `window.location.host` | **零改动**——后端挂载 `frontend/dist` 到同域即可（当前缺此挂载） |
| 运行时 | FastAPI + uvicorn + asyncio 子进程（ProactorEventLoop）+ SQLite | 均可被 PyInstaller 冻结，属成熟路径 |
| 隐患 | `adb.exe`/`*.jar` 被 .gitignore 排除；`./data/` 相对 CWD；端口硬编码 8765 与配置默认 8000 不一致 | 构建脚本显式注入二进制；exe 相对路径解析；端口可配置化（与既有待办合并） |

## 三、方案对比

| 方案 | 形态 | 增量开发量 | 取舍 |
|------|------|-----------|------|
| **A. PyInstaller onedir（推荐）** | 文件夹 + 双击 exe 自动开 Chrome | **5~7 人日** | 绿色免安装达标；产物 80~120MB（zip 后 30~50MB）；onedir 规避 onefile 的 Ctrl-C bug（PyInstaller #8817）并降低杀软误报 |
| B. A + Tauri 壳 | 原生窗口应用（托盘/单实例） | +4~6 人日（共 ~12 天） | 桌面应用体验最佳，但引入 Rust 工具链与 sidecar 生命周期管理；现阶段 YAGNI |
| C. A + pywebview | 简单窗口壳 | +1~2 人日（叠加在 A 上） | 折中方案；但 WebView2 跑高频二进制 WS 视频流需额外验证，有踩坑风险 |
| D. onefile 单文件 | 单个 .exe | 与 A 相当 | **不推荐**：信号处理 bug、启动解压慢 3~10s、杀软误报率最高 |

## 四、方案A 开发量分解

| # | 任务 | 人日 | 说明 |
|---|------|------|------|
| 1 | 冻结感知资源路径 | 1 | 新增 `runtime_paths.py`（`sys.frozen` 时以 exe 目录为根）；改造 `config/settings.py`（adb/yaml/db 路径）、`scrcpy/server_manager.py`（jar）、`run_server.py`（TEMP 清理） |
| 2 | 前端静态托管 | 0.5~1 | `main.py` 挂载 `StaticFiles(dist, html=True)` + SPA fallback；前端代码零改动 |
| 3 | 启动器体验 | 1 | `multiprocessing.freeze_support()`、无控制台模式（uvicorn 需 `use_colors=False/log_config=None`）、`webbrowser` 自动打开、端口占用时复用已有实例 |
| 4 | PyInstaller spec + 构建脚本 | 0.5~1 | `collect_submodules('uvicorn'/'pydantic')`；datas 注入 adb.exe、jar、config yaml、dist；产出发布 zip |
| 5 | 产物治理 | 0.5 | 版本号/图标/文件属性；杀软误报预案（onedir 已缓解，长期可考虑代码签名） |
| 6 | 干净环境验证 | 1~2 | 无 Python/Node/ADB 的 Windows VM 全功能回归：连接设备、H264 流、点击控制、5 个调试标签、录制导出 |
| 7 | 端口可配置化 | 0.5 | 与既有待办合并实施 |

合计 **5~7 人日**（单人约 1~1.5 周含缓冲）。

## 五、风险清单

- **杀软/SmartScreen 误报**：PyInstaller 产物的常态问题。onedir 形态 + zip 分发可缓解；面向外部用户分发时考虑代码签名。
- **Python 版本口径**：`pyproject.toml` 声明 `>=3.11`，本机实测 3.10.11 可运行——构建前需统一（建议构建环境 3.11+ 并修正声明）。
- **绑定地址与防火墙**：当前 `0.0.0.0` 触发 Windows 防火墙弹窗；绿色版默认 `127.0.0.1`，局域网访问留配置开关。
- **前端体积**：monaco + echarts 使 dist 较大；本地回环加载无感知，zip 体积 20MB 级。
- **依赖版本漂移**：uvicorn 0.53 引入的实验性 zttp 后端与本方案无关（未使用）；构建脚本应锁定依赖版本。

## 六、二次评估触发条件

满足以下条件后再评估实施：

1. 浏览器端到端测试通过（视频解码 + 输入控制全场景，含横竖屏）
2. 调试会话管理补齐（连接池/断线续传/会话恢复，当前 75%）
3. 远程 Shell PTY 落地（当前 75%）
4. 端口可配置化完成（打包前置依赖）
5. 集成测试与部署达到可发布标准（当前 30%）
6. 性能/网络数据持久化与下载（待排期需求）完成情况视用户对导出的诉求取舍

## 七、参考资料

- [onefile breaks uvicorn · PyInstaller #8817](https://github.com/pyinstaller/pyinstaller/issues/8817)
- [uvicorn + FastAPI + PyInstaller 启动失败讨论](https://github.com/Kludex/uvicorn/discussions/1820)
- [uvicorn workers>1 与 freeze_support](https://stackoverflow.com/questions/65438069/uvicorn-and-fastapi-with-pyinstaller-problem-when-uvicorn-workers1)
- [PyInstaller 排错文档（hidden imports）](https://pyinstaller.org/en/stable/when-things-go-wrong.html)
- [PyInstaller 常见问题与陷阱](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html)
- [noconsole 模式 uvicorn 崩溃修复](https://stackoverflow.com/questions/78978245/python-fastapi-no-console-mode-with-uvicorn)
- [PyInstaller 杀软误报说明](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/)
- [FastAPI 打包追踪 issue](https://github.com/tiangolo/fastapi/issues/2367)
- [FastAPI→exe 参考工程](https://github.com/iancleary/pyinstaller-fastapi)
- [uvicorn 发布说明](https://uvicorn.dev/release-notes/)
- [PyInstaller 手册](https://pyinstaller.org/en/stable/usage.html)
- [打包实战笔记](https://til.simonwillison.net/python/packaging-pyinstaller)
- [知乎：pyinstaller 打包 uvicorn 的坑](https://zhuanlan.zhihu.com/p/630072565)
