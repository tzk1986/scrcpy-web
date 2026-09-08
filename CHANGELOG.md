# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- 项目脚手架搭建
- 后端分层架构（Domain/Application/Infrastructure/Interface）
- 配置管理系统（YAML + Pydantic）
- 依赖注入框架
- 结构化日志（structlog）
- 设备管理基础功能
- 调试会话持久化框架
- Logcat 日志收集
- 远程 Shell 基础框架
- 前端 Vue 3 + TypeScript 项目
- 调试面板基础组件

### Changed
- 优化方案文档结构
- 建立进度追踪机制

## [0.1.0] - 2026-09-08

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
