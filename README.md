# OpenScrcpy

> scrcpy Web 化开源项目 —— 基于 Python (FastAPI) + Vue 3 的多设备远程控制与无限调试平台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Chrome Only](https://img.shields.io/badge/browser-Chrome%20only-4285f4.svg)](https://www.google.com/chrome/)

## ✨ 特性

- 🖥️ **零安装**：浏览器直接访问，无需客户端
- 🔍 **无限调试**：持久化 logcat、远程 Shell、性能/网络监控与采样数据导出
- 📱 **多设备管理**：同时管理 100+ 台 Android 设备
- 🎥 **低延迟视频流**：WebCodecs + WebSocket（Chrome 专属优化）
- 🤝 **团队协作**：屏幕共享、多人控制、实时标注（计划中）
- 🧪 **自动化测试**：操作录制回放、脚本沙箱（计划中）

## 🚀 快速开始

### 环境要求

- Python 3.11+
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
| Week 1-2 | 后端基础架构 | [01-后端基础架构.md](方案/01-后端基础架构.md) | ✅ 95%（含配置热重载） |
| Week 1-2 | 设备管理服务 | [02-设备管理服务.md](方案/02-设备管理服务.md) | ✅ 98% |
| Week 3-4 | 视频流服务 | [03-视频流服务.md](方案/03-视频流服务.md) | ✅ 98%（含自适应码率） |
| Week 3-4 | 前端视频播放器 | [04-前端视频播放器.md](方案/04-前端视频播放器.md) | ✅ 95% |
| Week 5-6 | 调试会话管理 | [05-调试会话管理.md](方案/05-调试会话管理.md) | ✅ 100%（含并发控制会话锁） |
| Week 5-6 | Logcat 日志系统 | [06-Logcat日志系统.md](方案/06-Logcat日志系统.md) | ✅ 98%（余语法高亮可选） |
| Week 5-6 | 远程 Shell | [07-远程Shell.md](方案/07-远程Shell.md) | ⏳ 90%（ConPTY 已通） |
| Week 5-6 | 性能监控 | [08-性能监控.md](方案/08-性能监控.md) | ✅ 95%（含网络监控） |
| Week 7-8 | 前端调试面板 | [09-前端调试面板.md](方案/09-前端调试面板.md) | ✅ 95%（5 标签页） |
| Week 7-8 | 集成测试与部署 | [10-集成测试与部署.md](方案/10-集成测试与部署.md) | ✅ 90%（地基版，CI 双 job 全绿；exe→#9） |

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

- 问题反馈：[GitHub Issues](https://github.com/yourusername/openscrcpy/issues)
- 讨论交流：[GitHub Discussions](https://github.com/yourusername/openscrcpy/discussions)

---

**项目状态**：🚧 积极开发中

**最新版本**：v0.1.0

**最新进展**：2026-09-24（方案 18-22 全部实施完成，其中 17-22 均经真机验收）

**下一步**：Windows 绿色免安装 exe 打包（见 `方案/15`）；[方案 18](方案/18-Perf与Network采样数据持久化与导出.md) 真机冒烟（录制 → 导出核对 → 暂存释放验证）
