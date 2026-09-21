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

- [x] B0 清理：删 examples.py（111 行死代码，零引用）；覆盖率基线确认
- [x] B1 `interfaces/ws/video.py`（miss 132 → ≥80%）：WS 协议层纯逻辑拆分测试（packet_mode 分支/首帧丢弃/config 拦截）
- [x] B2 `infrastructure/stream/scrcpy.py`（miss 247 → ≥75%）：mock adb 子进程/Push 流程/参数组装
- [x] B3 `application/app_service.py`（miss 171 → ≥85%）：mock AdbDriver
- [x] B4 `application/debug_service.py`（miss 174 → ≥80%）
- [x] B5 `infrastructure/persistence/sqlite.py`（miss 129 → ≥80%）：tmp db
- [x] B6 `infrastructure/network/sampler.py`（miss 127 → ≥80%）
- [x] B7 `infrastructure/adb/shell.py`（miss 160 → ≥75%）：PTY 会话逻辑 — **2026-09-21 补齐**：曾被全局门禁掩盖为虚假完成（实测 0%，160 语句全 miss）；新增 `tests/infrastructure/test_shell.py`（39 例）补至 **99%**，后端全局 84.32%→**88.78%**（提交 `12b85a0`）
- [x] B8 中头收尾：http/*、lifecycle、control_sender、adb/cli、reachability、deps、performance_service、session_service → 全局 ≥80%
- 批末：ruff / mypy strict 保持清零

### 前端（分层口径）

- [x] F1 stores：debug.ts 46%→80%、device.ts 0%→80%
- [x] F2 services：h264VideoStream / inputController →80%；api.ts 按可达性定
- [x] F3 关键组件 ≥70%：VideoPlayer、LogcatView、DebugPanel
- [x] F4 纯展示组件 mount 冒烟：AppsView / PerfView / NetworkView / ShellView / Dashboard / DeviceDetail / DockPanel / CommandPalette
- [x] F5 vitest.config 覆盖率 thresholds（核心口径 per-glob）+ CI 集成

### 文档（完整性 100%）

- [x] D1 API 文档 `docs/API.md`（HTTP 端点 + WS 协议清单）
- [x] D2 架构总览（`docs/架构.md`）
- [x] D3 用户手册 `docs/用户手册.md`（打包交付直接用）
- [x] D4 根目录整理：过时开发文档归档 `docs/archive/`（实际 **11 个**：7 根目录 + 4 docs/，原计划估 5 个）、backend*.log 清理

### 审查（AI 审查留档）

- [x] R1 流程固化：DEVELOPMENT.md 增补「变更审查」一节
- [x] R2 存量补审：方案 17 提交（`faf90a3`→`6a6f42e`）独立审查 → `docs/reviews/`
- [x] R3 各批次收尾增量审查（随批次）— 独立 AI 审查**通过**（0 阻塞 / 4 建议 / 11 观察），留档 `docs/reviews/2026-09-21-质量指标补全批次.md`；4 建议已处置（建议 1/2/4 修复、建议 3 暂缓记遗留，见留档「处置结果」）

## CI 门禁（收尾）

- [x] 后端：`pytest --cov=app --cov-fail-under=80`
- [x] 前端：vitest coverage thresholds（核心口径 per-glob）
- [x] 部署指南 / README 补覆盖率命令说明；`pyproject.toml` dev 依赖 pin `pytest-cov`；`package.json` 已含 coverage-v8

## 验收

- 后端全局 **88.78%**（≥80% 门禁；533 passed + 1 skipped）；前端核心口径达标（**95.93%**，分层门禁全过；294 passed）；门禁三件套（ruff/mypy/pytest-cov + eslint/vitest/build）全绿
- 文档三件套 + 根目录整理完成；审查留档就位（`docs/reviews/2026-09-21-质量指标补全批次.md`，结论通过 / 0 阻塞）
- 进度追踪第二十二次更新回写（已完成）；锁定的指标值回写进度追踪「质量指标」表（后端 88.78% / 前端 95.93%）

## 收尾修订（2026-09-21）

- **B7 虚假完成纠正**：批次执行中曾把全部 24 项一次性勾选，但 B7（`adb/shell.py`）实测 0% 覆盖（160 语句全 miss），被全局 84% 门禁掩盖。已如实回退并补齐：新增 `tests/infrastructure/test_shell.py`（39 例，假进程驱动 PTY 会话逻辑），shell.py 0%→99%，后端全局 84.32%→88.78%（提交 `12b85a0`）。教训：全局门禁会掩盖单模块缺口，分层/点名口径必要。
- **D4 数量纠正**：原计划估「5 个过时文档」，实际归档 11 个（7 根目录 + 4 docs/）。
- **R3 审查落地**：独立 AI 审查通过（0 阻塞 / 4 建议 / 11 观察）；建议 1/2/4 已修（注释/端口一致性，提交 `b445f36`），建议 3（transport 死模块删除）暂缓记遗留，`tests/integration/` 陈旧 POC 目录转记独立清理决策。