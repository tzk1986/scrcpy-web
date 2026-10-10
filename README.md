# OpenScrcpy

> scrcpy Web 化开源项目 —— 基于 Python (FastAPI) + Vue 3 的多设备远程控制与无限调试平台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Chrome Only](https://img.shields.io/badge/browser-Chrome%20only-4285f4.svg)](https://www.google.com/chrome/)

## ✨ 特性

- 🖥️ **零安装**：浏览器直接访问，无需客户端
- 🔍 **无限调试**：持久化 logcat、远程 Shell、性能/网络监控（含阈值告警）、采样数据导出
- 📱 **多设备管理**：同时管理 100+ 台 Android 设备
- 🎥 **低延迟视频流**：WebCodecs + WebSocket（Chrome 专属优化）
- 🤝 **团队协作**：屏幕共享、多人控制、实时标注（计划中）
- 🧪 **自动化测试**：操作录制回放、脚本沙箱（计划中）

## 🚀 快速开始

### Windows 绿色版（推荐，零安装）

从 [Releases](https://github.com/tzk1986/scrcpy-web/releases) 下载
`OpenScrcpy-win64-<版本>.zip`，解压到任意可写目录后双击 `OpenScrcpy.exe`，
服务启动后自动打开浏览器（默认 http://127.0.0.1:8765）。除 Chrome 外**零安装**
（adb、scrcpy-server、Python 运行时全部随包分发）。用法、端口/单实例、
退出服务与故障排查详见 [Windows exe 发布说明](docs/Windows-exe-发布说明.md)。

### 环境要求（源码运行）

- Python 3.10+
- Node.js 20+
- ADB (Android Debug Bridge)
- Chrome 浏览器（最新两个稳定版）

### 开发模式

```bash
# 安装后端依赖（含 dev 工具链：pytest/ruff/mypy）
pip install -e ".[dev]"

# 安装前端依赖
cd frontend && npm ci
cd ..

# 启动开发服务器（后端 8765 + 前端 8080）
make dev
```

访问：
- 前端：http://localhost:8080
- 后端：http://localhost:8765
- API 文档：http://localhost:8765/docs

> 后端端口默认 8765，可用环境变量 `BACKEND_PORT`（或仓库根 `.env`）覆盖，
> 前端代理与 E2E 自动跟随。详见 [部署指南](docs/部署指南.md)。

### Docker 部署（开发参考，未验证）

Docker/Linux 配置仅作服务器化方向的骨架，**未在 Linux 容器实测**（后端依赖
ConPTY/pywinpty、scrcpy.exe、ProactorEventLoop 等 Windows 专属组件）。当前受支持
的运行方式为 **Windows 本地运行**，详见 [部署指南](docs/部署指南.md)。

## 📖 文档

### 核心文档

- [项目总览](方案/00-总览.md) - 项目定位、技术栈、实施路线
- [用户手册](docs/用户手册.md) - 面向使用者的操作指南（随交付分发）
- [架构总览](docs/架构.md) - 分层结构、依赖方向、关键数据流
- [API 参考](docs/API.md) - HTTP 端点、WebSocket 协议、二进制流协议
- [部署指南](docs/部署指南.md) - 运行方式、端口配置、配置热重载、本地 E2E、CI、已知限制
- [Windows exe 发布说明](docs/Windows-exe-发布说明.md) - 绿色版使用、退出服务三路径、故障排查（随包分发）
- [经验记录](docs/经验记录.md) - 协议/环境/调试踩坑与解决方案
- [归档文档](docs/archive/README.md) - 历史实施记录索引

### 实施方案

详细的分模块实施方案：

- [方案目录](方案/README.md) - 所有方案文档索引
- [进度追踪](方案/进度追踪.md) - 实时进度跟踪
- [实施指南](方案/实施指南.md) - 开发流程、调试技巧

### 模块方案

| 周次 | 模块 | 文档 | 状态 |
|------|------|------|------|
| Week 1-2 | 后端基础架构 | [01-后端基础架构.md](方案/01-后端基础架构.md) | ✅ 100%（含配置热重载；启动/关闭钩子已闭环） |
| Week 1-2 | 设备管理服务 | [02-设备管理服务.md](方案/02-设备管理服务.md) | ✅ 100%（真机集成验证闭环） |
| Week 3-4 | 视频流服务 | [03-视频流服务.md](方案/03-视频流服务.md) | ✅ 100%（含自适应码率；性能打磨 P0–P2 全线收口 + P3 日志降噪，方案 26–33） |
| Week 3-4 | 前端视频播放器 | [04-前端视频播放器.md](方案/04-前端视频播放器.md) | ✅ 100%（含追帧/丢帧策略与 PTS 透传，方案 31/32；旋转坐标 e2e 已真机通过） |
| Week 5-6 | 调试会话管理 | [05-调试会话管理.md](方案/05-调试会话管理.md) | ✅ 100%（含并发控制会话锁） |
| Week 5-6 | Logcat 日志系统 | [06-Logcat日志系统.md](方案/06-Logcat日志系统.md) | ✅ 100%（含虚拟滚动 + 语法高亮，方案 25） |
| Week 5-6 | 远程 Shell | [07-远程Shell.md](方案/07-远程Shell.md) | ✅ 100%（ConPTY + 历史 + Tab 补全 + 彩色输出 + 终端 resize 全链路真机验证） |
| Week 5-6 | 性能监控 | [08-性能监控.md](方案/08-性能监控.md) | ✅ 100%（含网络监控 + 阈值告警（方案 24）） |
| Week 7-8 | 前端调试面板 | [09-前端调试面板.md](方案/09-前端调试面板.md) | ✅ 100%（5 标签页 + 停靠/快捷键/命令面板/响应式） |
| Week 7-8 | 集成测试与部署 | [10-集成测试与部署.md](方案/10-集成测试与部署.md) | ✅ 100%（CI 三 job 全绿；exe 已实施（方案 15/23）；v0.7.3 正式发布（2026-10-09）；Docker/E2E-CI 已决策例外） |

专项方案：

| 编号 | 文档 | 状态 |
|------|------|------|
| 16 | [设备连接可达性预检优化](方案/16-设备连接可达性预检优化.md) | ✅ 已实施（不可达 21.2s→1.55s） |
| 17 | [推流流畅度优化](方案/17-推流流畅度优化.md) | ✅ 已实施并真机验证 |
| 18 | [Perf/Network 采样数据持久化与导出](方案/18-Perf与Network采样数据持久化与导出.md) | ✅ 已实施（暂存/缓存两级 + 录制开关 + 双限清理 + CSV/JSON 导出） |
| 19 | [推流连接稳定性优化](方案/19-推流连接稳定性优化.md) | ✅ 已实施并真机验收（空闲保活 / 卡死探针自愈 / 回切关键帧 / WS 重连加固） |
| 20 | [推流 HARD 超时误判修复](方案/20-推流HARD超时误判修复.md) | ✅ 已实施并真机复测（健康推流不再 17s 呼吸式回退） |
| 21 | [自适应码率静止误判治理](方案/21-自适应码率静止误判治理.md) | ✅ 已实施并真机验收（降档回弹复核 + 冷静期，600s 零误切换） |
| 22 | [恢复探针判据相位敏感修复](方案/22-恢复探针判据相位敏感修复.md) | ✅ 已实施并真机验收（卡死注入 5/5 收敛，出图 0.2-0.3s） |
| 15 | [Windows 绿色 exe 打包](方案/15-Windows绿色exe打包方案.md) | ✅ 已实施并全链路真机验证（2026-09-28，zip 19.0MiB，发布说明随包） |
| 23 | [绿色版退出机制与单开模式](方案/23-绿色版退出与单开模式方案.md) | ✅ 已实施（2026-09-28，UI 退出 + stop.bat 兜底 + 端口探测 D1 修复） |
| 24 | [性能阈值告警](方案/24-性能阈值告警方案.md) | ✅ 已实施（2026-09-28，SDD 五任务 + 全分支终评；后端判定 + WS/轮询内嵌 alerts + 前端 toast/徽标条，不落库） |
| 25 | [Logcat 虚拟滚动与语法高亮](方案/25-Logcat虚拟滚动与语法高亮方案.md) | ✅ 已实施（2026-09-29，DynamicScroller 虚拟滚动 + 自研 tokenizer，5 万条真机验证） |
| 26 | [冗余拷贝链优化](方案/26-冗余拷贝链优化方案.md) | ✅ 已实施（2026-09-30，后端包协议直通 + 前端扫描/组装合一，常态媒体帧零整帧拷贝） |
| 27 | [截图模式操作反馈优化](方案/27-截图模式操作反馈优化方案.md) | ✅ 已实施（2026-09-30，事件驱动截屏，反馈 2491→1116ms 2.2×） |
| 28 | [视频流启动延迟与流复用优化](方案/28-视频流启动延迟与流复用优化方案.md) | ✅ 已实施（2026-09-30，JAR bak 常驻恢复，10s 级 push 尖峰结构性摘除） |
| 29 | [截图模式设备端编码链路换挡](方案/29-截图模式设备端编码链路换挡方案.md) | ✅ 已实施（2026-09-30，screencap\|gzip -1 raw 直读，照片级 673.5ms <800ms 达成） |
| 30 | [静止误判探测减速](方案/30-静止误判探测减速方案.md) | ✅ 已实施（2026-09-30，递增冷静期 600→3600s，真机 40 分钟验收闭环） |
| 31 | [前端丢帧策略与 warn 风暴治理](方案/31-前端丢帧策略与warn风暴治理.md) | ✅ 已实施（2026-09-30，追赶状态机锁定至下一 IDR，恢复 0.91–1.11s） |
| 32 | [PTS 全链透传](方案/32-PTS全链透传方案.md) | ✅ 已实施（2026-10-08，WS 8B PTS 前缀 + WebCodecs timestamp 正确化，真机验收闭环） |
| 33 | [后端流日志降噪](方案/33-后端日志降噪方案.md) | ✅ 已实施（2026-10-08，SPS/PPS 幂等重发等 7 项精准降级，预期活跃流 INFO 降 ~75%） |
| 34 | [低帧率设备探针误判治理](方案/34-低帧率设备探针误判治理方案.md) | ✅ 已实施并真机验收（2026-10-09，判死窗解耦加宽 10s + 并发防踩停 token 制 + 注入单例修复；.33 风暴复测 20 针 0/20 越阈） |
| 35 | [设备列表慢设备阻塞治理](方案/35-设备列表慢设备阻塞治理方案.md) | ✅ 已实施并真机验收（2026-10-09，慢设备查询并发化 + 短路超时 + 冷却负缓存；32.2s 全表阻塞降至秒级） |
| 36 | [网段扫描连接与设备缩略图](方案/36-网段扫描连接与设备缩略图方案.md) | ✅ 已实施并真机验收（2026-10-10，POST /api/devices/scan 网段扫描批量连接 + 设备列表屏幕缩略图（TTL 缓存 + 并发池）；三台真机 spike 画面一致，终评修复轮后复评 Approved） |

## 🏗️ 项目结构

```
scrcpy-web/
├── backend/                 # Python 后端
│   └── app/
│       ├── core/           # 横切关注点（配置、日志、异常）
│       ├── domain/         # 领域层（纯业务逻辑）
│       ├── application/    # 应用层（用例编排）
│       ├── infrastructure/ # 基础设施层（具体实现）
│       ├── interfaces/     # 接口层（HTTP/WebSocket）
│       ├── scrcpy/         # scrcpy 协议/编解码/常量
│       ├── deps.py         # 依赖注入
│       ├── lifecycle.py    # 生命周期管理
│       └── main.py         # 应用工厂
├── frontend/                # Vue 3 前端
│   └── src/
│       ├── components/     # 组件
│       ├── views/          # 页面
│       ├── stores/         # Pinia 状态管理
│       ├── services/       # API 服务
│       └── router/         # 路由
├── tests/                   # 全部测试（unit/scrcpy/application/infrastructure/e2e/manual）
├── config/                  # 配置文件
│   ├── base.yaml           # 默认配置
│   ├── dev.yaml            # 开发环境
│   └── prod.yaml           # 生产环境
├── docker/                  # Docker 配置（开发参考，未验证）
├── docs/                    # 技术文档（含部署指南）
├── scripts/                 # 工具脚本
├── 方案/                    # 实施方案文档
├── .github/workflows/       # CI 流水线
├── Makefile                 # 开发命令
├── docker-compose.yml       # Docker 编排（开发参考，未验证）
└── pyproject.toml          # Python 项目配置
```

## 🎯 核心架构

### 分层架构

```
┌─────────────────────────────────────────┐
│         Interface Layer (接口层)          │
│   HTTP Endpoints / WebSocket / gRPC      │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│        Application Layer (应用层)         │
│   Use Cases / Orchestration              │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│          Domain Layer (领域层)            │
│   Entities / Value Objects / Ports       │
└─────────────────────────────────────────┘
                    ↑
┌─────────────────────────────────────────┐
│      Infrastructure Layer (基础设施层)     │
│   Adapters / Implementations             │
└─────────────────────────────────────────┘
```

### 设计原则

1. **依赖倒置**：业务逻辑依赖抽象接口，不依赖具体实现
2. **配置分离**：所有参数集中在 `config/`，不改代码
3. **可测试性**：每一层都可以独立测试
4. **可替换性**：核心组件可以热插拔替换

## 🔧 技术栈

### 后端

- **框架**：FastAPI + uvicorn
- **ORM**：aiosqlite（SQLite）
- **设备通信**：ADB CLI / scrcpy-server
- **日志**：structlog
- **配置**：pydantic-settings + pyyaml

### 前端

- **框架**：Vue 3 + TypeScript
- **构建**：Vite
- **状态**：Pinia
- **UI**：Element Plus
- **终端**：xterm.js
- **图表**：ECharts
- **视频**：WebCodecs API

### 前端关键技术

- WebCodecs（H.264 解码，支持 `?swdecode=1` 切换软解）
- WebSocket（视频帧 / 日志 / 性能指标流传输）
- WebTransport（保留占位，尚未启用）

## 📊 性能指标

| 指标 | 目标值 |
|------|--------|
| 视频延迟 | < 150ms（局域网）|
| 日志延迟 | < 100ms |
| Shell 响应 | < 50ms |
| 日志吞吐 | 5000 条/秒 |
| 内存占用 | < 200MB（前端）|

> 实测（2026-09-30）：视频端到端延迟**中位数 76ms**（5 样本 65–83ms，.18 设备 / 局域网 / 4Mbps / 30fps）。
> 性能打磨阶段 **P0–P2 优化已全线收口**（任务泄漏/队列反压/拷贝链/截图反馈/启动复用/自适应误判/丢帧策略/PTS 透传，共方案 26–32），**P3 日志降噪已收口**（方案 33）。
> 方法与基线见 `docs/性能对标与优化清单.md`。

## 🧪 测试

所有测试位于仓库根 `tests/` 目录（禁止放在 `backend/` 或项目根下）：

```bash
# 后端测试（默认套件，排除需真机的 e2e；含覆盖率门禁 80%）
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e -v --cov=app --cov-fail-under=80

# 前端单元测试（含分层覆盖率门禁：services/stores ≥80%，关键组件 ≥70%）
cd frontend && npx vitest run --coverage

# 端到端测试（需连接 Android 设备，在仓库根运行）
npm run test:e2e

# 本地 CI 门禁（等价于 GitHub Actions）
make ci
```

CI：push/PR 到 `master` 触发 `.github/workflows/ci.yml`（E2E 不进 CI，仅本地跑）。

## 📝 开发指南

### 添加新功能

1. 阅读 [实施指南](方案/实施指南.md)
2. 参考对应模块的方案文档
3. 遵循分层架构
4. 编写测试
5. 更新文档

### 代码规范

- Python：遵循 PEP 8，使用 ruff 格式化
- TypeScript：遵循 Vue 3 风格指南
- 提交信息：使用 [Conventional Commits](https://www.conventionalcommits.org/)

## 🤝 贡献

欢迎贡献！请参考 [贡献指南](CONTRIBUTING.md)（待创建）。

## 📄 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

- [scrcpy](https://github.com/Genymobile/scrcpy) - 优秀的 Android 投屏工具
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Web 框架
- [Vue](https://vuejs.org/) - 渐进式 JavaScript 框架

## 📮 联系方式

- Bug 报告：[GitHub Issues](https://github.com/tzk1986/scrcpy-web/issues)（带版本号、Windows 版本、设备信息与复现步骤）
- 讨论交流：[GitHub Discussions](https://github.com/tzk1986/scrcpy-web/discussions)

反馈时强烈建议附上日志片段：绿色版位于包目录 `logs/openscrcpy.log`，源码版为运行终端输出。

---

**项目状态**：🚧 积极开发中

**最新版本**：v0.7.3（2026-10-09 正式发布）

**最新进展**：2026-10-09 正式发版 **v0.7.3** —— 含方案 35「设备列表慢设备阻塞治理」（慢设备不再阻塞全表，连接页/管理页秒级响应）与方案 34「低帧率设备探针误判治理」全链收口（判死窗解耦加宽 + 并发防踩停 token 制 + 注入单例修复；.33 真机风暴复测 20 针 0/20 越阈，切页/双会话/前端阈值四项验收全 PASS）

**下一步**：绿色版试用反馈收集；设备 offline 场景治理评估（方案 34 D5 暂缓项）；Phase 2 规划（网络抓包等）
