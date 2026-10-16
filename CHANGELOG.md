# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.10] - 2026-10-16（验证包，未发布）

### Added

- 设备备注/别名快速定位（方案 50）：已连接设备可为其设置备注（≤64 字符，存 user.yaml `devices.<id>.alias`，跨断线重连与重启持久）；Dashboard 设备 ID 列别名优先两行展示（别名+灰字序列号，留空回落序列号）；操作列「备注」按钮行内编辑（确定保存/空串清除/取消不动）；表格上方新增搜索框按 备注/型号/设备ID 三字段子串过滤（大小写不敏感）；设置页设备级覆盖下拉标题带别名

### Fixed

- 设置页设备聚焦与下拉标题长期失效（方案 45 T8 遗留）：`api.listDevices` 真实返回键为 `id`，前端曾误按 `device_id` 取值——`?device=` 参数聚焦与设备下拉选中实际未生效；本期修正取值并以真实响应形状 mock 钉死

## [0.7.9] - 2026-10-16

### Added

- 截图标注选中拖拽缩放（方案 47 ①）：已放置标注点选后出现 8 向缩放手柄，拖动等比/非等比变换（Shift 锁等比），参数（字号/线宽/透明度）随缩放联动 clamp；操作历史重构为五类步骤快照（add/delete/transform/edit-text/clear），移位落定一步可撤销、清空整体回放、删除原位插回
- Android 11+ 无线调试配对（方案 47 ②）：「添加设备」对话框新增「无线调试配对」页签两步式——步骤 1 填弹窗显示的 ip:配对端口+6 位配对码执行 `adb pair`（端点层校验 IPv4/端口范围/纯 ASCII 6 位码，失败按旧版 adb、配对码错误、其他原因三类回显中文文案），步骤 2 输设备主页面调试端口复用 connect；后端 `pair_tcp` 原语 + `POST /api/devices/pair`（预检不可达不进子进程）
- 设置参数说明手册（方案 47 ③）：`/settings/help` 手册页四组 10 条参数（串流/录屏/设备覆盖/通用），每条含含义、调大/调小双向影响与适合场景、范围、生效时机；设置页各参数标签挂「?」圆标锚点直达对应条目，页头提供手册入口，手册页智能返回设置页
- 起流前设备残留 scrcpy 会话检测（方案 48）：`ScrcpyEncoder.start` 前置检查设备端在跑的 scrcpy-server（旧 toybox ps 的 NAME 列无 scrcpy 字样，改为设备侧 `grep /proc/*/cmdline`，正则 `[.]` 防自匹配）——命中阻断起流并经 WS error 消息回中文指引，按启动参数指纹归因「本软件上次会话残留」或「其他工具（如 scrcpy 桌面版）占用」；grep 不可用的老 ROM 降级放行不阻断。同时治理残留根因（D2）：起流成功后枚举本会话设备端 PID，`stop()` 追加设备侧 `kill -9`（原实现只杀本地 adb 子进程，设备端进程异常退出时残留）
- 领域异常经视频 WS 原样透传（方案 48）：`OpenScrcpyException` 的 message 本身即面向用户的中文文案（如占用指引），不再与内部异常一样脱敏为 internal error；其余内部异常维持方案 37 A4 脱敏口径不变

### Changed

- 截图标注矩形/椭圆选中优化（方案 49）：点击图形内部区域即可选中拖动（原仅边框条命中——内部被刻意排除以便「在旧图形内画新图形」，按用户反馈拖动优先、反转该取舍；重叠图形仍按最上层优先命中，上层先拖）
- 截图工具栏移除「重做」按钮（方案 49 案 A）：重做常态置灰、低价值且与撤销重复感强，功能保留 `Ctrl+Shift+Z` 快捷键；撤销/清空按钮不变

- 页面切换流畅优化：Dashboard 重进静默刷新——从录屏/设置等页面切回时，store 已有设备列表则挂载不再触发 v-loading 遮罩（旧列表即时保留、后台数据到后无感换血，SSE 增量感知机制不变），仅冷启动空列表时显示加载遮罩；「刷新」按钮行为保持（主动刷新照常给加载反馈）
- 顶部菜单取消溢出折叠（`el-menu :ellipsis="false"`）：「设置」等菜单项常驻直接可点，不再收进「…」；后续功能按钮增多时再评估主动折叠

### Fixed

- 设备连接假成功治理（路线图第 15 项）：`adb connect` 对已登记地址回 `already connected`（并不尝试重连、不代表链路可用）旧代码按字符串判成功——若该 transport 实际处于 offline/unauthorized（状态行会被设备列表过滤），用户见「连接成功」提示但列表为空。现 already connected 分支补一次 `adb devices` 状态复核：offline → 提示设备端 adbd 异常并给出重开网络调试/kill-server 指引；unauthorized → 提示在设备端确认授权弹窗；不在表 → 提示先断开再重连（400 DEVICE_NOT_READY 中文文案）。新连接成功路径零额外开销；扫描编排自动将此类地址归入 failed 并带原因
- 设备页「断开」误回设置/录屏页：断开原用 `history.back()`，经「查看录屏 / 设置 → 返回设备」进入时返回按钮的 `push` 垫栈致落点错误；现断开显式回 Dashboard，录屏/设置页「返回设备」改智能导航（上一条历史恰为目标设备页时 `back` 不垫栈，直达或菜单带参进入则回落 `push`）

## [0.7.8] - 2026-10-15（正式发布：tag v0.7.8 + GitHub Release；0.7.7 为验证包跳号未 tag）

### Added

- 录屏产物独立页面（方案 46）：全局 `/recordings` 页历史录屏列表（扫盘为准，含旧格式兼容）——播放弹层 / 下载 / 导出 webm / 删除（二次确认，占用时响亮报错）；落盘命名嵌设备标识（`openscrcpy_rec_{dev}__{id}.mp4`）；设备页 done 态收敛为「已保存 + 查看录屏 + 重新录制」一行，产物处理不再挤占设备页

## [0.7.7] - 2026-10-14（验证包，未发布 Release）

### Added

- 方案 45「设置页 / codec 放开 / 检查更新 / 设备级覆盖」：配置分层新增用户覆盖层 `config/user.yaml`（base → user → {env} → 环境变量，watcher 无条件监听热重载）；`GET/PUT /api/settings` 读写接口（pydantic 白名单校验 + 原子写 + 损坏/只读响亮报错保旧配置）；独立设置页 `/settings`（运行参数 / 生效参数含来源徽标 / 运行环境 / 设备级覆盖四卡片，顶部菜单 + 设备页 `?device=` 跳转入口）；codec 后端放开 h265（`video_codec=opts.codec` + WS 侧 HEVC NAL 解析与 hvcC 配置下发 + 前端 WebCodecs 解码链路按 family 泛化、`isConfigSupported` 浏览器门控）；scrcpy 检查更新（仅手动触发查 GitHub Releases latest 四态提示，不自动下载）；设备级参数覆盖（user.yaml `devices.<id>` 节，流启动/录屏 start 消费，未覆盖键回落全局）

### Fixed

- 打包版打开设置页弹 cmd 窗口一闪而没：runtime-env 的 adb 版本探测 spawn 补 `CREATE_NO_WINDOW`（windowed 宿主为控制台子进程新建窗口，方案 15 同款问题在方案 45 新增点的回归）；窗口抑制回归测试由固定文件清单改为全树扫描 `backend/app`，新增派发点自动纳入门禁

## [0.7.6] - 2026-10-13（正式发布 2026-10-14：tag v0.7.6 + GitHub Release）

### Added

- 截图标注编辑器二次设计（方案 43）：字号/线宽比例制可调——工具栏滑块 + 画布滚轮实时调参，预览与导出同观感（修复屏幕越大导出的字号/线宽相对越小）；undo/redo 双栈（Ctrl+Z / Ctrl+Shift+Z）；新增椭圆、荧光笔、序号气泡、马赛克 4 种工具（共 8 种，数字键 1-8 快切）；已放置标注命中选中、拖动移动、Delete 删除、双击文字原地编辑；标注结果一键复制到剪贴板（按钮 / Ctrl+C）
- 录屏流程优化与磁盘清理（方案 42）：设备端自然录满自动收尾（监听 screenrecord 进程退出，消除「白录」等待）；停止兜底 pull 增强（表项保留 + 应用内文件探测）；录制会话滞留自愈（超时清理 + 状态校正）；应用关服统一清理录制会话与暂存、启动即扫描清理磁盘残留；录屏 done 态完备化（GET 列表收敛失效条目、补齐 file_size、前端大小行展示与完成后直接重录）

## [0.7.5] - 2026-10-12（验证包，未发布 Release）

### Added

- 设备文件管理（方案 39）：新增独立页 `/device/:id/files`（设备详情入口）——目录浏览（面包屑导航、符号链接目标显示、大小/时间/权限列）、上传（多文件顺序单飞、真实百分比 + adb 段诚实不确定态进度）、下载（单文件原名 / 多选打包 zip）、重命名（非法名前端先校验）、删除（多选强确认、目录递归警示、逐条结果汇总）；后端 6 个 HTTP 端点 + FileService（写白名单 /sdcard 与 /data/local/tmp、路径净化、shlex.quote 防注入、设备级传输互斥、暂存用完即清 + TTL/孤儿清扫）

### Fixed

- 截图预览弹窗竖屏长图被拉伸（0.7.4 发布后修复收编）：canvas 按比例封顶渲染

## [0.7.4] - 2026-10-12

### Added

- 网段扫描批量连接（方案 36，合并自 0.7.4-内测包）：扫描栏支持单个 IP / CIDR 网段（/22~32）/ 起止区间三种写法，TCP 探测开放端口后批量 adb 连接（单台短超时、64 台上限截断、扫描互斥 409）；扫描记录独立卡片（浏览器持久化）+ 多选批量连接与列表勾选批量断开
- 设备列表屏幕缩略图（方案 36）：4:3 外框自适应裁切、点击放大预览、悬停查看快照时间；在线设备自动加载（TTL 30s 缓存 + 并发池 3 + 失败不自动重试），「刷新缩略图」手动强制重拉
- 设备录屏（方案 37 屏幕工具）：详情页「录屏」一键录制（设备端 screenrecord，60 秒上限兜底 + 红点计时）→ 停止后自动拉取，面板内预览/下载 MP4；单设备单录互斥（第二路 409）；低端设备编码失败自动降档 256x144 重试
- webm 短片段导出（方案 37 屏幕工具）：录屏预览截取前 10 秒（10 帧/秒）导出短片（captureStream + MediaRecorder，零依赖）；浏览器不支持时按钮降级置灰
- 截图标注（方案 37 屏幕工具）：「截图」预览弹窗支持画笔/箭头/矩形/文字叠加标注，导出标注后 PNG 或直接保存原图
- 应用运行时状态（方案 37 应用检查）：详情弹窗「运行时」区块——Top Activity / Resumed Activity 卡片 + Service 清单 + 按需刷新；解析缺失字段返回 parse_warning 提示，不整体失败
- 性能监控扩展（方案 37 性能增强）：进程 CPU Top 8 表格 + 电池温度（摄氏直显）/电压/健康状态展示条（单项缺失自动隐藏）；采样与 CSV/JSON 导出新增 4 字段（旧字段零变化，新字段置尾）
- 详情页一键截图（方案 40）：视频流抓帧主路径 + 无流回退设备端截图，截图更接近实时画面

### Security

- 安全审查专项（方案 37 安全审查）：Origin/Host 守卫中间件收敛浏览器 CSRF 攻击面（本地同源/白名单放行，无 Origin 头客户端豁免，其余 403）；导出文件名统一净化（防路径穿越/盘符/隐藏文件）；WS 消息解析加固（非法消息归一、字段类型校验、错误栈脱敏）；命令注入面防护（winpty 元字符拒绝 + device_id/包名白名单）；session_id 改 uuid4 随机标识

### Fixed

- 录屏降档竞态（方案 37 屏幕工具）：低端设备编码器报错延迟超启动宽限窗时降档不生效——扩展「宽限窗 + 任务终结回调」双触发点并防重入（.25 真机复验通过）
- 截屏方向矫正（方案 38/40）：截图链路 rotation 补正（含 .33 rotation=1 情形、root 截屏魔数剥前缀兜底、多屏警告污染治理）；rotation 判据信号源换挡 dumpsys input→display，元数据缺失时按 deviceInfo rotation 兜底
- 断开重连方向颠倒修复（方案 41）：rotation 缓存随连接生命周期清理，修复断开-重连后缩略图方向颠倒
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
