# OpenScrcpy 项目指令

> **重要**：本文档是 AI 助手（Claude）和项目共享的指令文档。开发前必须阅读 `DEVELOPMENT.md` 对齐所有约束，避免目标偏离。

## 开发前必做

1. **阅读 `DEVELOPMENT.md`** — 完整的开发约束文档（核心约束、架构约束、代码规范、开发流程）
2. **检查 `方案/进度追踪.md`** — 确认当前任务状态
3. **阅读对应的方案文档** — 理解目标和实现步骤
4. **遇到协议/环境/调试问题先查阅 `docs/经验记录.md`** — 已踩过的坑和解决方案，避免重复踩坑

## 测试文件约束

**所有测试文件必须放在 `tests/` 目录下。**

目录结构：
- `tests/unit/` — 单元测试（不依赖外部服务）
- `tests/integration/` — 集成测试（可能需要数据库、设备等）
- `tests/scrcpy/` — scrcpy 相关模块测试
- `tests/application/` — 应用层服务测试
- `tests/infrastructure/` — 基础设施层测试

禁止在项目根目录或 `backend/` 下创建测试文件。运行测试：
```bash
PYTHONPATH=backend python -m pytest tests/ -v
```

## 项目结构

```
backend/app/          — Python 后端（FastAPI）
  domain/             — 领域层（模型、端口）
  application/        — 应用层（服务）
  infrastructure/     — 基础设施层（ADB、SQLite、scrcpy）
  interfaces/         — 接口层（HTTP、WebSocket）
frontend/src/         — Vue 3 前端
tests/                — 所有测试文件
docs/                 — 技术文档
方案/                 — 进度追踪和方案文档
```

## 启动命令

```bash
# 后端
python run_server.py    # 端口 8765，已配置 Windows ProactorEventLoop

# 前端开发
cd frontend && npm run dev    # 代理到 8765

# 运行测试
PYTHONPATH=backend python -m pytest tests/ -v
```

## 关键约定

- Windows 兼容：asyncio 子进程需要 ProactorEventLoop（已在 run_server.py 配置）
- Git Bash 中执行 ADB shell 命令时设置 `MSYS_NO_PATHCONV=1` 避免路径转换
- 后端端口 8765（避免与常用端口冲突）
- WebSocket 消息协议：JSON 格式，`op` 字段为客户端操作，`type` 字段为服务端消息类型
- **浏览器约束**：仅支持 Chrome（最新两个稳定版），可使用 WebCodecs、WebTransport 等特性
- **架构约束**：严格四层架构，禁止跨层调用，依赖倒置通过 Protocol 实现
- **问题修复流程**：无法一次性修复的问题，必须先调研（官方文档/源码）、分析根因、输出方案文档、再实施。禁止盲改代码试错。详见 `DEVELOPMENT.md` 约束 7
