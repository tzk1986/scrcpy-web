# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-29（pre-release 首发）

### Added
- 多设备管理：连接/断开、SSE 实时监听、可达性预检（不可达 21.2s→1.55s）
- 视频流：H.264 + WebCodecs 低延迟投屏、自适应码率、断流自愈与回退守卫
- 调试会话：SQLite 持久化、断线续传、并发锁
- Logcat：实时流/分级过滤/导出、DynamicScroller 虚拟滚动（5 万条实测）、自研语法高亮
- 远程 Shell：xterm.js + ConPTY、命令历史/Tab 补全/彩色输出/断线重连/终端 resize
- 性能/网络监控：实时图表、阈值告警、采样数据持久化与 CSV/JSON 导出
- 前端调试面板：5 标签页、可停靠/拖拽、快捷键、命令面板、响应式布局
- Windows 绿色版（解压即用，19.0MiB zip）：随包 adb、UI 退出 + stop.bat 兜底、单开模式
- 质量体系：CI 三 job 全绿、覆盖率门禁（后端 92.35%、前端 95.4%）、AI 独立审查机制
- 浏览器 E2E（Playwright）与部署/用户/API/架构文档

### Changed
- 项目文档结构化：模块方案体系 + 进度追踪日志（38 次更新）

## [0.0.1] - 2026-09-08（初始脚手架，历史条目）

### Added
- Initial project setup
- Basic architecture design
- Development environment configuration
- Implementation plan documents

---

## 版本说明

- **Added** for new features
- **Changed** for changes in existing functionality
- **Deprecated** for soon-to-be removed features
- **Removed** for now removed features
- **Fixed** for any bug fixes
- **Security** in case of vulnerabilities

## 发布流程

1. 更新版本号（pyproject.toml, package.json）
2. 更新 CHANGELOG.md
3. 提交代码
4. 打 Git tag
5. 发布 GitHub Release
