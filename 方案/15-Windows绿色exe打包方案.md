# 15 - Windows 绿色 exe 打包方案（调研存档）

> **状态：二次评估完成（2026-09-24）——方案 A 可用，待排期实施**
> **决策（2026-09-17）**：当前阶段优先完成现有版本的功能实现与体验优化；待功能与体验达标后，以本文档为基础进行**二次评估**再决定是否实施打包。
> **二次评估（2026-09-24）**：§六 六条触发条件已全部满足；代码走查发现 2 项打包硬前置与若干修订点，外部经验补充 9 类坑，详见 §八。**两项硬前置已实施完成并提交。**
> **第三批确认（2026-09-24）**：外置文件全部可随包分发（用户除 Chrome 外零安装）；体积预估 onedir ≈45MB / zip ≈22MB；第三批走查新增 11 项发现，详见 §九。
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

## 八、二次评估（2026-09-24）

### 8.1 结论

**方案 A（PyInstaller onedir + 前端静态同域托管 + 自动开浏览器）仍然可用，前置条件已全部满足，可进入实施排期。**
原调研的核心判断（架构对打包友好、改造量小、onedir 优于 onefile）经 7 个月代码演进后依然成立；但代码走查发现 **2 项硬前置**（§8.3）与若干需修订的实施细节（§8.5），外部调研补充了 9 类此前未覆盖的坑（§8.4）。

### 8.2 触发条件复核（§六 六条）

| # | 条件 | 现状（2026-09-24） | 判定 |
|---|------|-------------------|------|
| 1 | 浏览器端到端测试通过 | E2E 9/9 100%（本地跑，不入 CI） | ✅ |
| 2 | 调试会话管理补齐 | 方案 05 100%（连接池/断线续传/会话恢复/会话锁） | ✅ |
| 3 | 远程 Shell PTY 落地 | 方案 07，ConPTY 已接入（`infrastructure/adb/winpty.py`） | ✅ |
| 4 | 端口可配置化 | `BACKEND_PORT`/`BACKEND_HOST` 生效（`config/settings.py:199`，vite 代理与 E2E 跟随） | ✅ |
| 5 | 集成测试与部署达可发布标准 | 方案 10 地基版，CI 双 job 全绿 | ✅ |
| 6 | 性能/网络持久化与导出 | 方案 18 已完成 | ✅ |

### 8.3 硬前置（实施前必须先做，否则绿色版功能残缺）

> **均已实施完成（2026-09-24）**，门禁全绿（ruff/mypy strict、pytest 681 passed / 覆盖率 91.85%）。

1. **统一裸 `adb` 调用（新发现，原方案遗漏）**：`infrastructure/stream/scrcpy.py` 10 处 + `scrcpy/server_manager.py` 3 处直接用字符串 `"adb"`（依赖系统 PATH），绕过了 `settings().adb.path` 的 tools 优先检测（`infrastructure/adb/cli.py`、`shell.py` 才走配置）。打包绿色版的用户机器无 adb 时，视频流/控制/server 推送全部失败。需先将这 13 处统一走 `settings().adb.path`，冻结感知才有意义。——**已完成**：13 处全部替换，新增 2 个回归测试锁定（自定义路径生效断言）。
2. **Python 版本口径**：`pyproject.toml` 声明 `>=3.11`，但本机仅有 Python 3.10.11，且全部真机验证均在该版本完成。建议**以 3.10.11 构建并实测**（保持与验证环境一致），同步修正声明为 `>=3.10`；若要用 3.11+ 打包，需重跑真机回归。——**已完成**：`requires-python`、ruff `target-version=py310`、mypy `python_version=3.10` 三处降级；CI backend job 改为 `[3.10, 3.11]` 矩阵（3.10 = 打包环境口径，3.11 = 上游声明版本）。

### 8.4 外部经验补充（2026-09-24 调研，原方案未覆盖）

| # | 坑 | 做法 | 来源 |
|---|-----|------|------|
| 1 | pywinpty 无官方 hook（hooks-contrib 686 个 hook 里没有 winpty） | 显式注入 winpty 包内 4 个非扩展二进制：`conpty.dll`、`winpty.dll`、`OpenConsole.exe`、`winpty-agent.exe`（`_winpty.cp310-win_amd64.pyd` 会被自动收集） | [pywinpty #536](https://github.com/andfoy/pywinpty/issues/536) |
| 2 | 冻结 bootloader 会 `SetDllDirectoryW` 把 DLL 搜索路径指向包目录，子进程继承 → 启动 adb.exe 可能 DLL 冲突 | 启动外部程序前执行 `ctypes.windll.kernel32.SetDllDirectoryW(None)` | [PyInstaller Common Issues](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html) |
| 3 | `--noconsole` 下 stdout/stderr 为 None，uvicorn 默认 StreamHandler 写入即崩；bootloader 启动期错误连 stderr 都没有 | 自定义 `log_config` 落文件（structlog 一并落文件）；排错用 DebugView | [同上](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html) |
| 4 | pydantic v2 元数据缺失（`PackageNotFoundError`） | 升级 pyinstaller + hooks-contrib 到最新（内置 hook-uvicorn / hook-pydantic 已适配 v2），必要时 `copy_metadata('pydantic')`；**不必手写 collect_submodules 长清单** | [hook-pydantic.py](https://github.com/pyinstaller/pyinstaller-hooks-contrib/blob/master/_pyinstaller_hooks_contrib/stdhooks/hook-pydantic.py) |
| 5 | PyInstaller 6.x onedir 资源全部进 `_internal`，老写法 `os.path.dirname(sys.executable)` 定位资源失效 | 资源路径用 `sys._MEIPASS`（6.x onedir 下即 `_internal`）；spec 的 datas 用 `src:dest` 写法；用户数据禁写 `_MEIPASS` | [CHANGES 6.0.0](https://pyinstaller.org/en/stable/CHANGES.html) |
| 6 | 绿色版体验坑 | 单实例 `CreateMutex` + 已存在则直接开浏览器指向已有端口；端口先试绑、失败回退并写回配置；**绑 `127.0.0.1` 不弹防火墙**（绑 0.0.0.0 必弹）；`webbrowser.open` 需等端口就绪 | [webbrowser 文档](https://docs.python.org/3/library/webbrowser.html) |
| 7 | 数据目录只读场景 | exe 同级 `data/` 优先（VSCode Portable 范式），装在 Program Files 等只读位置时回落 `%LOCALAPPDATA%` | [PortableApps 规范](https://portableapps.com/platform/features) |
| 8 | UPX 压缩的报毒特征与启动代价 | **关闭 UPX**（体积省 ~30% 但启动变慢、更易报毒）；如必须用则 `--upx-exclude` 只排除问题文件 | [Using UPX](https://pyinstaller.org/en/stable/usage.html) |
| 9 | onefile 的三重劣势（已在 §三 决策，此处补充实据） | 启动慢 3-8 倍、`%TEMP%\_MEIxxxx` 强杀残留、自解压+UPX 是启发式报毒特征 → 维持 onedir 决策 | [Operating Mode](https://pyinstaller.org/en/stable/operating-mode.html) |

**高置信度结论**（多来源一致）：onedir 不用 onefile；干净 venv + 上游 hook（不手写清单）；noconsole 自管文件日志；Windows 强制 Proactor 策略 + `freeze_support()` 前置；外部程序先 `SetDllDirectoryW(None)` 并用绝对路径；绑回环 + 单实例 + 端口试绑；关 UPX。

### 8.5 原方案任务分解的修订

| 原任务 | 修订 |
|--------|------|
| （新增）任务 0 | 统一 13 处裸 `adb` 调用（§8.3-1）——打包硬前置，也是独立健壮性修复——**已完成**（2026-09-24） |
| （新增）任务 0.5 | `scripts/fetch_tools.py`：从官方源下载 + SHA256 校验 adb.exe 与 scrcpy-server.jar（§9.3-6）——发布构建可复现的前置；pyproject 删除零使用的 `aiofiles`（§9.3-7）顺手完成 |
| 1 冻结感知资源路径 | 增加：winpty 4 个二进制注入（§8.4-1）；配置/data 路径"exe 同级优先 + APPDATA 回落"（§8.4-7）**必须同时改 `config_watch.py` 的 `CONFIG_DIR`**（§9.3-4）；`scrcpy_recordings` TEMP 清理经查为**死代码**（backend 无生产者），可随改造删除 |
| 2 前端静态托管 | 不变（`main.py` 确无 `StaticFiles` 挂载，仍待实施；前端 `/api` 相对路径 + `location.host` 已确认零改动）；**SPA fallback 为必须项**（history 路由实测确认，§9.3-10） |
| 3 启动器体验 | 增加：noconsole 文件日志（§8.4-3）、`SetDllDirectoryW(None)`（§8.4-2）、单实例 mutex + 端口试绑回退（§8.4-6）；绑定地址改 `127.0.0.1`（防火墙 + WebCodecs 安全上下文双重理由，§9.3-5）；**入口改为 `from app.main import app` 传对象**（§9.3-1） |
| 4 spec + 构建脚本 | 修订：依赖上游 hook（升级工具链即可），**不手写 collect_submodules**；关 UPX；`src:dest` datas 写法并按 §9.1 表格对齐注入目标；`pathex` 含仓库根 + `backend/`（§9.3-2）；`hiddenimports=['app.main']`（§9.3-1）；`excludes=['rich','pygments','tkinter','pytest','pywin32']`（§9.3-8）；构建顺序 `npm ci && npm run build` → PyInstaller（§9.3-11） |
| 5 产物治理 | 不变；补：打包前清理死配置（`scrcpy_path`/`jwt_secret`，§9.3-9） |
| 6 干净环境验证 | 不变；补充"存疑项实测"（见下）；构建环境用**干净 venv**（本机 dev 环境已漂移，§9.3-7） |
| 7 端口可配置化 | **已完成**（2026-09-24 复核），无需再排 |

另：§五 风险清单"局域网访问留配置开关"一条按 §9.3-5 修正——局域网访问不只是防火墙问题，`http://` 非安全上下文下 WebCodecs 不可用（视频退化为截图模式），绿色版定位为**本机工具**（`127.0.0.1`），局域网视频需求须另行 HTTPS 方案。

### 8.6 待实测清单（外部调研存疑项）

1. pywinpty 在 PyInstaller 下二进制的收全情况（含 Win10 <1809 无 ConPTY 回退 winpty 后端的路径）
2. hook-pydantic 在锁定版本 + 构建 Python 组合下是否完全免手工干预
3. Nuitka 第二候选对照（构建耗时、uvicorn/pywinpty 资源收全率）——本项目纯 Python 依赖为主，PyInstaller 仍是首选
4. 免安装目录位于 OneDrive / 网络盘 / 中文路径时的行为
5. 目标环境杀软（Defender/360/火绒）误报实测与代码签名成本收益
6. 自动开浏览器 + 端口回退 + 服务就绪的时序竞态（慢机需重试策略）

### 8.7 其他复核记录（数字更新）

- 前端 `dist` 实测 **2.3MB**（原估"zip 20MB 级"偏高；monaco/echarts 已 code-split）
- 开发库 `data/debug.sqlite` 已有 128MB + WAL 270MB（长期调试积累）——发布包**不含 data/**，首次运行建空库
- `config/settings.py:91` 的 `StreamConfig.scrcpy_path` 仍为死配置（不依赖 scrcpy.exe），可随手清理
- `config/base.yaml` `server.host: 0.0.0.0` → 打包版默认改 `127.0.0.1`

## 九、外置文件分发、体积预估与走查补充（2026-09-24 第三批）

> 结论前置：**除 Chrome 浏览器外，用户不需要安装任何东西**——adb、server jar、前端静态资源、配置 yaml、Python 运行时与全部 Python 依赖都随包分发。

### 9.1 外置文件随包分发清单

各外置文件的注入目标**必须与冻结后 `__file__` 派生的解析位置一致**（这是资源定位的唯一依据）：

| 外置文件 | 体积 | 当前来源 | spec datas 注入目标 | 冻结后解析路径 | 用户是否需自装 |
|---------|------|-------------------------------|-------------------|--------------|-------------|
| `tools/adb.exe` | 6.6MB | 本地 tools/（来源无记录，见 9.3-6） | `tools` | `_detect_adb_path()`：`_internal/tools/adb.exe` | 否 |
| `scrcpy-server.jar` | 0.73MB | `backend/app/scrcpy/`（v4.1，对齐 `SCRCPY_SERVER_VERSION`） | `app/scrcpy` | `server_manager.__file__` 同目录 | 否 |
| 前端 `dist/` | 2.3MB | `frontend/dist`（构建产物，未入库） | `frontend/dist` | 任务 2 的 runtime_paths | 否 |
| `config/*.yaml` | ~10KB | 仓库 `config/`（已入库） | `config` | `config.settings.__file__` 同目录（热重载共用） | 否 |
| Python 运行时 + 依赖 | 约 35MB | — | PyInstaller 自动收集 | — | 否 |
| `scrcpy.exe` / Node / ffmpeg | — | **不需要**（后端只用 jar 直连协议；前端已构建为静态资源） | — | — | 否 |

**零代码改动路径（推荐）**：adb 注入 `_internal/tools/` 即可被现有 `_detect_adb_path()` 找到，无需改 `config/settings.py`。
**可选增强**：在 `_detect_adb_path()` 增加"exe 同级 `tools/` 优先"分支，便于用户自行替换 adb 版本（与 data/config 的"exe 同级优先"策略一致）。

**实测补充**：`adb.exe` 单文件脱离 platform-tools 目录可独立运行（实测 `adb version` → 1.0.41 / 36.0.0，退出码 0），TCP 设备（`adb connect ip:5555`）无需 `AdbWinApi.dll`/`AdbWinUsbApi.dll`；**USB 直连场景**才需要这两个 DLL（若要支持 USB，从 platform-tools 补入约 0.2MB 并加设备接入文档，未实测）。

### 9.2 体积预估（2026-09-24 本机实测口径）

取数方法：Python 3.10.11 本机实测（`site-packages` 按 pyproject 声明集计量；二进制按 1:1 计入，纯 Python 源码编译进 PYZ 后按经验压缩比折算）：

| 组成 | 实测/估算 | 冻结后 | 说明 |
|------|----------|--------|------|
| 解释器（python310.dll 4.25 + vcruntime 0.13 + base_library.zip） | — | ~5.9MB | 1:1 |
| stdlib C 扩展/DLL 子集（libcrypto 3.29 / sqlite3 1.44 / unicodedata 1.07 / _ssl 等） | 8.23MB | ~8.2MB | 1:1，不含 tcl/tk |
| 第三方二进制（pydantic_core 5.0 / pywinpty 6.5 / PyYAML 0.26 / orjson 0.21 / websockets / httptools / watchfiles） | ~13.6MB | ~13.6MB | 1:1 |
| 第三方纯 Python（约 12MB 源码） | 12MB | ~6–8MB | 编译进 PYZ（压缩） |
| 本项目后端 + config | ~0.4MB | ~0.2MB | PYZ |
| exe bootloader | — | ~1MB | |
| 资产（adb 6.6 + jar 0.73 + dist 2.3 + yaml） | 9.65MB | 9.65MB | 1:1 |
| **onedir 目录合计** | | **≈45MB（区间 40–52MB）** | |
| **发布 zip（deflate）** | | **≈22MB（区间 19–26MB）** | 二进制约压至 40%、JS 资产约压至 35% |

- 原方案 §三 估"产物 80~120MB / zip 30~50MB"**偏保守**：本项目无 numpy/OpenCV 类科学栈，前端 dist 实测仅 2.3MB（code-split 生效）。
- 不确定度 ±20%：base_library 与 stdlib DLL 子集由 PyInstaller 分析决定，**精确值需一次真实构建才能钉死**（可做一次性 spike 构建，产物落临时目录）。
- 可选瘦身：spec `excludes=['rich','pygments','tkinter','pytest','pywin32']` 约省 **9MB+**（structlog 对 rich 是可选导入，会优雅回落，须在干净 venv 实测确认）。
- 运行时数据另计：SQLite 库随调试数据增长（本机已见 128MB + WAL 270MB），发布包不含 `data/`，首次运行建空库（§8.7）。

### 9.3 走查补充：第三批发现与处置

第三批走查（两个硬前置完成后）新发现 11 项，全部并入 §8.5 任务分解：

| # | 发现 | 影响 | 处置 |
|---|------|------|------|
| 1 | `run_server.py:47` 以字符串 `"app.main:app"` 启动，PyInstaller 静态分析**收不到 `app.main`** | exe 启动即崩（Could not import module） | 任务 3：入口改为 `from app.main import app` 后传对象；spec 加 `hiddenimports=['app.main']` 双保险 |
| 2 | `app` 与 `config` 是**两个顶层包**（靠 `sys.path` 注入，非包内相对），spec 若不显式声明 `pathex` 则两者都收不到 | exe 启动崩 | 任务 4：spec `pathex` 显式含仓库根与 `backend/` |
| 3 | datas 注入目标必须与 9.1 表格一一对应（`__file__` 派生解析） | 资源找不到（静默回落或功能残缺） | 任务 1 + 任务 4 按 9.1 表格执行 |
| 4 | `config_watch.py:33` 的 `CONFIG_DIR` 是**独立常量**（不复用 settings 加载路径） | 若只改 yaml 加载路径，"exe 同级 config 优先"不生效：热重载仍监听 bundle 内文件 | 任务 1 增补该文件，两处必须同步 |
| 5 | **`VideoDecoder` 规范级要求安全上下文**：W3C WebCodecs IDL 为 `[Exposed=(Window,DedicatedWorker), SecureContext]`（一手实证） | 局域网 `http://192.168.x.x` 下浏览器不暴露 WebCodecs → 视频流退化为截图模式（控制仍可用）；局域网访问**不只是防火墙问题** | 方案定调：绿色版仅本机 `127.0.0.1`；局域网视频如需，必须 HTTPS（超出本方案范围） |
| 6 | `adb.exe`/`scrcpy-server.jar` 均被 gitignore 排除且**无获取脚本** | 发布构建不可复现（换机器/CI 无法构建） | 新增任务：`scripts/fetch_tools.py` 从官方源下载 + SHA256 校验并锁定版本（jar 4.1 对齐常量） |
| 7 | 本机 dev 环境与 pyproject **漂移**：`aiofiles` 已声明但未安装且**代码零使用**；`httptools`/`watchfiles`（`uvicorn[standard]` 附加项）未安装；`pywin32`(17.9MB)/`rich`/`pygments` 为未声明依赖 | 体积预估与依赖收集**必须以干净 venv 为基准**，不能以本机环境为基准 | 构建脚本：干净 venv `pip install .`；pyproject 删除零使用的 `aiofiles`；预估按声明集口径（9.2） |
| 8 | `structlog` import 期即拉入 `rich`+`pygments`（+9.2MB 纯 Python，rich 属可选依赖） | 白白增大体积 | 任务 4 spec excludes（见 9.2 瘦身项） |
| 9 | 死配置：`StreamConfig.scrcpy_path`（含 `D:/scrcpy-win64-v4.1/...`）、`SecurityConfig.jwt_secret`（JWT 零使用，含 `change-me-in-production`）、`cors_origins` 仅 dev 用 | 发布包遗留误导性配置 | 打包前顺手清理（§8.7 已含 scrcpy_path，补 JWT 一项） |
| 10 | 前端确认 `createWebHistory()`（history 路由，实测） | 深链接/刷新 404 | 任务 2 的 SPA fallback 为**必须项**（非可选） |
| 11 | `frontend/dist` 未入库；`data/` 目录由 `sqlite.py:67` 自动 mkdir（实测） | 打包顺序依赖前端构建；数据目录无需额外处理 | 任务 4 明确构建顺序：`npm ci && npm run build` → PyInstaller |
