# 质量指标补全实施计划（exe 打包前置）

> 目标：打包前完成质量基础建设，避免打包后返工。
> 决策（2026-09-19，用户拍板）：
> - 前端覆盖率分层口径：services/stores ≥80% 硬指标 + 关键组件（VideoPlayer/LogcatView/DebugPanel）≥70% + 纯展示组件 mount 冒烟；CI 门禁按核心口径卡
> - 代码审查落地：AI 审查留档（每批收尾独立审查 → `docs/reviews/`），并补审方案 17 存量提交
> - 覆盖率达标后入 CI 门禁防回退

## 基线（2026-09-19 实测）

- 后端 51%（3657 stmts / miss 1779）；测试 219 passed + 1 skipped
- 前端 26.58%（全 src 口径，v8）；58 passed；核心逻辑 40~100% 不均
- 顺手修复：npm install peer 冲突（`2b9cb88`）

## 批次

### 后端（目标：全局 ≥80%，预估 +60~90 用例）

- [ ] B0 清理：删 examples.py（111 行死代码，零引用）；覆盖率基线确认
- [ ] B1 `interfaces/ws/video.py`（miss 132 → ≥80%）：WS 协议层纯逻辑拆分测试（packet_mode 分支/首帧丢弃/config 拦截）
- [ ] B2 `infrastructure/stream/scrcpy.py`（miss 247 → ≥75%）：mock adb 子进程/Push 流程/参数组装
- [ ] B3 `application/app_service.py`（miss 171 → ≥85%）：mock AdbDriver
- [ ] B4 `application/debug_service.py`（miss 174 → ≥80%）
- [ ] B5 `infrastructure/persistence/sqlite.py`（miss 129 → ≥80%）：tmp db
- [ ] B6 `infrastructure/network/sampler.py`（miss 127 → ≥80%）
- [ ] B7 `infrastructure/adb/shell.py`（miss 160 → ≥75%）：PTY 会话逻辑
- [ ] B8 中头收尾：http/*、lifecycle、control_sender、adb/cli、reachability、deps、performance_service、session_service → 全局 ≥80%
- 批末：ruff / mypy strict 保持清零

### 前端（分层口径）

- [ ] F1 stores：debug.ts 46%→80%、device.ts 0%→80%
- [ ] F2 services：h264VideoStream / inputController →80%；api.ts 按可达性定
- [ ] F3 关键组件 ≥70%：VideoPlayer、LogcatView、DebugPanel
- [ ] F4 纯展示组件 mount 冒烟：AppsView / PerfView / NetworkView / ShellView / Dashboard / DeviceDetail / DockPanel / CommandPalette
- [ ] F5 vitest.config 覆盖率 thresholds（核心口径 per-glob）+ CI 集成

### 文档（完整性 100%）

- [ ] D1 API 文档 `docs/API.md`（HTTP 端点 + WS 协议清单）
- [ ] D2 架构总览（`docs/架构.md`）
- [ ] D3 用户手册 `docs/用户手册.md`（打包交付直接用）
- [ ] D4 根目录整理：5 个过时开发文档归档 `docs/archive/`、backend*.log 清理

### 审查（AI 审查留档）

- [ ] R1 流程固化：DEVELOPMENT.md 增补「变更审查」一节
- [ ] R2 存量补审：方案 17 提交（`faf90a3`→`6a6f42e`）独立审查 → `docs/reviews/`
- [ ] R3 各批次收尾增量审查（随批次）

## CI 门禁（收尾）

- [ ] 后端：`pytest --cov=app --cov-fail-under=80`
- [ ] 前端：vitest coverage thresholds（核心口径 per-glob）
- [ ] 部署指南 / README 补覆盖率命令说明；`pyproject.toml` dev 依赖 pin `pytest-cov`；`package.json` 已含 coverage-v8

## 验收

- 后端全局 ≥80%；前端核心口径达标；门禁三件套全绿
- 文档三件套 + 根目录整理完成；审查留档就位
- 进度追踪第二十二次更新回写；锁定的指标值回写进度追踪「质量指标」表