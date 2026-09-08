# OpenScrcpy

> scrcpy Web 化开源项目 —— 基于 Python (FastAPI) + Vue 3 的多设备远程控制与无限调试平台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Chrome Only](https://img.shields.io/badge/browser-Chrome%20only-4285f4.svg)](https://www.google.com/chrome/)

## ✨ 特性

- 🖥️ **零安装**：浏览器直接访问，无需客户端
- 🔍 **无限调试**：持久化 logcat、远程 Shell、性能监控、网络抓包
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
# 克隆项目
cd D:\tangzk\py\scrcpy-web

# 安装后端依赖
pip install -e ".[dev]"

# 安装前端依赖
cd frontend && npm ci

# 启动开发服务器
make dev
```

访问：
- 前端：http://localhost:5173
- 后端：http://localhost:8000
- API 文档：http://localhost:8000/docs

### Docker 部署

```bash
docker compose up -d
```

访问：http://localhost

## 📖 文档

### 核心文档

- [项目总览](方案/00-总览.md) - 项目定位、技术栈、实施路线
- [架构设计](docs/architecture.md) - 分层架构、核心模块、设计原则
- [API 文档](docs/api.md) - REST API 和 WebSocket 接口
- [调试功能](docs/debug-features.md) - 无限调试功能详解

### 实施方案

详细的分模块实施方案：

- [方案目录](方案/README.md) - 所有方案文档索引
- [进度追踪](方案/进度追踪.md) - 实时进度跟踪
- [实施指南](方案/实施指南.md) - 开发流程、调试技巧

### 模块方案

| 周次 | 模块 | 文档 | 状态 |
|------|------|------|------|
| Week 1-2 | 后端基础架构 | [01-后端基础架构.md](方案/01-后端基础架构.md) | ✅ 90% |
| Week 1-2 | 设备管理服务 | [02-设备管理服务.md](方案/02-设备管理服务.md) | ⏳ 40% |
| Week 3-4 | 视频流服务 | [03-视频流服务.md](方案/03-视频流服务.md) | ⏳ 20% |
| Week 3-4 | 前端视频播放器 | [04-前端视频播放器.md](方案/04-前端视频播放器.md) | ⏳ 10% |
| Week 5-6 | 调试会话管理 | [05-调试会话管理.md](方案/05-调试会话管理.md) | ⏳ 30% |
| Week 5-6 | Logcat 日志系统 | [06-Logcat日志系统.md](方案/06-Logcat日志系统.md) | ⏳ 30% |
| Week 5-6 | 远程 Shell | [07-远程Shell.md](方案/07-远程Shell.md) | ⏳ 20% |
| Week 5-6 | 性能监控 | [08-性能监控.md](方案/08-性能监控.md) | ⏳ 0% |
| Week 7-8 | 前端调试面板 | [09-前端调试面板.md](方案/09-前端调试面板.md) | ⏳ 40% |
| Week 7-8 | 集成测试与部署 | [10-集成测试与部署.md](方案/10-集成测试与部署.md) | ⏳ 0% |

## 🏗️ 项目结构

```
scrcpy-web/
├── backend/                 # Python 后端
│   ├── app/
│   │   ├── core/           # 横切关注点（配置、日志、异常）
│   │   ├── domain/         # 领域层（纯业务逻辑）
│   │   ├── application/    # 应用层（用例编排）
│   │   ├── infrastructure/ # 基础设施层（具体实现）
│   │   ├── interfaces/     # 接口层（HTTP/WebSocket）
│   │   ├── deps.py         # 依赖注入
│   │   ├── lifecycle.py    # 生命周期管理
│   │   └── main.py         # 应用工厂
│   └── tests/              # 测试
├── frontend/                # Vue 3 前端
│   ├── src/
│   │   ├── components/     # 组件
│   │   ├── views/          # 页面
│   │   ├── stores/         # Pinia 状态管理
│   │   ├── services/       # API 服务
│   │   └── router/         # 路由
│   └── tests/              # 测试
├── config/                  # 配置文件
│   ├── base.yaml           # 默认配置
│   ├── dev.yaml            # 开发环境
│   └── prod.yaml           # 生产环境
├── docker/                  # Docker 配置
├── docs/                    # 文档
├── scripts/                 # 工具脚本
├── 方案/                    # 实施方案文档
├── Makefile                 # 开发命令
├── docker-compose.yml       # Docker 编排
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

### Chrome 专属优化

- WebCodecs（H.264 解码）
- SharedArrayBuffer（零拷贝）
- WebTransport（低延迟传输）
- FileSystem Access（本地文件）

## 📊 性能指标

| 指标 | 目标值 |
|------|--------|
| 视频延迟 | < 150ms（局域网）|
| 日志延迟 | < 100ms |
| Shell 响应 | < 50ms |
| 日志吞吐 | 5000 条/秒 |
| 内存占用 | < 200MB（前端）|

## 🧪 测试

```bash
# 后端单元测试
pytest backend/tests/unit

# 后端集成测试
pytest backend/tests/integration

# 前端测试
cd frontend && npm run test

# E2E 测试
cd frontend && npm run test:e2e
```

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

**最新版本**：v0.1.0 (2026-09-08)

**下一步**：完成设备管理服务 → 视频流集成 → 无限调试功能
