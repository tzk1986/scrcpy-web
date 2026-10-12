# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.4] - 2026-10-12

### Added

- 网段扫描批量连接（方案 36，合并自 0.7.4-内测包）：扫描栏支持单个 IP / CIDR 网段（/22~32）/ 起止区间三种写法，TCP 探测开放端口后批量 adb 连接（单台短超时、64 台上限截断、扫描互斥 409）；扫描记录独立卡片（浏览器持久化）+ 多选批量连接与列表勾选批量断开
- 设备列表屏幕缩略图（方案 36）：4:3 外框自适应裁切、点击放大预览、悬停查看快照时间；在线设备自动加载（TTL 30s 缓存 + 并发池 3 + 失败不自动重试），「刷新缩略图」手动强制重拉
- 设备录屏（方案 37 屏幕工具）：详情页「录屏」一键录制（设备端 screenrecord，60 秒上限兜底 + 红点计时）→ 停止后自动拉取，面板内预览/下载 MP4；单设备单录互斥（第二路 409）；低端设备编码失败自动降档 256x144 重试
- webm 短片段导出（方案 37 屏幕工具）：录屏预览截取前 10 秒（10 帧/秒）导出短片（captureStream + MediaRecorder，零依赖）；浏览器不支持时按钮降级置灰
- 截图标注（方案 37 屏幕工具）：「截图」预览弹窗支持画笔/箭头/矩形/文字叠加标注，导出标注后 PNG 或直接保存原图
- 应用运行时状态（方案 37 应用检查）：详情弹窗「运行时」区块——Top Activity / Resumed Activity 卡片 + Service 清单 + 按需刷新；解析缺失字段返回 parse_warning 提示，不整体失败
- 性能监控扩展（方案 37 性能增强）：进程 CPU Top 8 表格 + 电池温度（摄氏直显）/电压/健康状态展示条（单项缺失自动隐藏）；采样与 CSV/JSON 导出新增 4 字段（旧字段零变化，新字段置尾）

### Security

- 安全审查专项（方案 37 安全审查）：Origin/Host 守卫中间件收敛浏览器 CSRF 攻击面（本地同源/白名单放行，无 Origin 头客户端豁免，其余 403）；导出文件名统一净化（防路径穿越/盘符/隐藏文件）；WS 消息解析加固（非法消息归一、字段类型校验、错误栈脱敏）；命令注入面防护（winpty 元字符拒绝 + device_id/包名白名单）；session_id 改 uuid4 随机标识

### Fixed

- 录屏降档竞态（方案 37 屏幕工具）：低端设备编码器报错延迟超启动宽限窗时降档不生效——扩展「宽限窗 + 任务终结回调」双触发点并防重入（.25 真机复验通过）
- 方案 36 终评修复轮收口：端口 422 校验、扫描 60s 超时上界修正、缩略图清理竞态等 10 项

## [0.7.3] - 2026-10-09

### Added
- 设备列表慢设备阻塞治理（方案 35）：单设备信息查询 4 命令并发化 + 快命令 5s 短路超时（慢设备触顶时 `GET /api/devices` 全表阻塞 32.2s → 秒级）、查询失败兜底保留条目、30s 冷却负缓存、查询任务复用
- `DeviceInfo.slow` 瞬态字段 + 设备管理页「adb 慢」红灯标识（设备恢复后毫秒级消除）

### Changed
- 前端设备列表刷新移出交互关键路径：断开/跳转即时响应，列表静默重拉（代次守卫防覆盖）

### Fixed
- 慢设备阻塞引发的连锁卡顿：连接页→设备管理页加载久、断开按钮转圈（0.7.2 实测反馈）

## [0.7.1 ~ 0.7.2] - 2026-10-08（方案 34 内测包，未发布 Release）

- **0.7.1**：低帧率设备探针误判治理（方案 34）全链实施——判死窗解耦加宽
  `max(2×idle_reset, 10s)`、探针发送失败有界兜底（连续 3 次判死）、stall
  重启决策器样本作废 + 10s 上报宽限期、claim 清残留 pending、acquire/release
  token 持有制（防并发踩停）、前端 STALL_MS 联动 `2×keepalive+2s`、stall
  原因区分与探针应答延迟采样
- **0.7.2**：注入服务单例缺陷修复（D8）——`get_stream_service`/
  `get_session_service` 补 `@lru_cache()`，根治多会话并发各起 encoder 互踩
  （设备端 SIGABRT 重启循环）。0.7.3 起含方案 34 §四全部真机验收收口
  （.33 风暴复测 20 针 0/20 越阈、切页/双会话/前端阈值四项 PASS）

## [0.7.0] - 2026-09-29（pre-release 首发）

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
- 项目文档结构化：模块方案体系 + 进度追踪日志（39 次更新）

### Fixed
- 首发版本号订正：初发 v0.1.0 与仓库历史 tag（v0.2.0~v0.6.0）冲突（违反语义版本连续性），撤销后改发 v0.7.0

## [0.1.0 ~ 0.6.0] - 2026-09-08 ~ 2026-09-11（早期开发迭代，历史补录）

早期开发冲刺的迭代版本标记（tag 留存于仓库，未发布 Release；条目据 tag message 与 DEVELOPMENT.md 迭代记录补录）：

- v0.1.0（09-08）：项目脚手架、四层分层架构、配置系统、设备管理基础、调试会话/Logcat/Shell 基础框架
- v0.2.0（09-09）：测试文件统一入 `tests/`（约束 6）+ 流式 Shell 输出；同 tag message 记调试面板功能（应用管理/性能/网络监控）
- v0.3.0（09-09）：视频流修复 + 经验沉淀；次日补 v0.3.0-debug-optimization（录制控制/搜索/视频自适应/性能采样）
- v0.4.0（09-11）：视频流优化 + PTY Shell + 画面比例修复
- v0.5.0（09-11）：视频画面按比例显示 + 坐标映射修复 + 视频流调试日志
- v0.6.0（09-11）：H.264 视频流端到端调通 + 点击控制修复 + scrcpy v4.1 升级

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
   - 同步订正：`backend/app/__init__.py` `__version__`、`config/settings.py` 默认值、`config/base.yaml`、`scripts/build_exe.sh` VERSION、`frontend/package-lock.json` 顶层两处、`tests/unit/test_config_reload.py` 期望值、`docs/用户手册.md` 适用版本
   - 全仓核对：`grep -rn "<旧版本号>" --exclude-dir={node_modules,dist,.git} .`（计划文档与更新日志中的历史快照除外）
2. 检查 tag 历史连续性：`git tag -l --format='%(refname:short) | %(creatordate:short)' | sort -V` —— 新版本号必须为历史最高，不得倒退
   - 教训（2026-09-29）：首发曾误用 v0.1.0，与历史开发 tag（v0.2.0~v0.6.0）冲突，发布后被迫撤销订正为 v0.7.0
3. 更新 CHANGELOG.md
4. 提交代码
5. 打 Git tag
6. 发布 GitHub Release
