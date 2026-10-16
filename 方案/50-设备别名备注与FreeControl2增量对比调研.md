# 方案 50：设备备注/别名（快速定位设备）+ FreeControl2 增量对比调研

> **状态**：**已实施**（2026-10-16，T1 后端 `5fde282` + T2 前端 `f64b454` + T3
> 文档回写 `2da87a6` + 终评加固 `3732805`（终评 PASS_WITH_NOTES 无阻塞）；0.7.10 验证包
>   已打包（2026-10-16，zip 26,471,456 B / SHA256 `915cf3bf…922e`，静态核验 + 8766 API
>   冒烟全过，进度追踪九十六次）；因追加半死设备呈现层治理（`8caa42e`）改打 **0.7.11
>   验证包**（同日，zip 26,471,830 B / SHA256 `db31e723…ba13`，别名链路 + 治理特征均核验
>   在包，进度追踪九十八次）；同日追加 .22 类 rotation=1 缩略图 90/270 过补偿修复
>   （`28faed3`）覆盖重打，最终包 26,471,433 B / SHA256 `1aee6b34…9469`（进度追踪九十九次）——
>   **随 v0.7.11 正式发布**（2026-10-16，tag + GitHub Release 附最终包，push origin + gitlab）。拍板记录：用户指示「按推荐设置进行
> 方案落盘，并进行实施」→ D1=A user.yaml devices 节 / D2=留空回落序列号（不采「设备N」）/
> D3=备注+型号+设备ID 三字段搜索 / D4=本期仅 Dashboard 表列 + 设置页下拉标题）。
> 编号注记：本文原编号 51，因截图修复方案（原 50，文件已删）被用户指示撤销而同位改号。
> 用户 2026-10-16 立项指令原话：「还有一个已连接设备增加一个功能，备注，可以通过
> 备注快速找到对应的设备，方便使用，不用一直记录，只要当前设备连接了，能够添加
> 即可，可以参照FreeControl2中的别名方式，看是否可以实现，另外对应FreeControl2
> 和现有项目进行对比调研，看是否还有功能可以借鉴」。
> **前提**：方案 45 已完成 FreeControl2「参数设置 / 运行环境」双维度全量对比
> （其结论与借鉴项不重复评估）；本文只做**增量**：别名机制深研 + 其余功能面
> 扫掠。
> **约束**：零新依赖；master 直做；门禁同例。

## 一、FreeControl2 别名机制调研（源码实读 2026-10-16）

来源：`pdone/FreeControl2`（Rust+Tauri+Svelte），关键文件
`src-tauri/src/config.rs`、`src-tauri/src/device.rs`、`src/lib/store.ts`、
`src/components/DevicePage.svelte`。

| 机制 | 做法 | 对本项目的适用性 |
|------|------|------------------|
| 数据模型 | `Device { id, alias: String, kind: usb/wifi, serial, ip, port }`，alias 为必填字符串，落 `%APPDATA%/FreeControl2/config.json` | 形态差异：对方是**显式设备登记表**（离线仍保留、重启不丢，有专测 `aliases_and_connection_ids_survive_restart`）；本需求「连接了能添加即可」→ **无需登记表**，按 device_id 挂元数据即可 |
| 默认名 | 新增时留空自动 `设备N`（`alias: alias.trim() || \`设备${len+1}\``，config.rs 671 行断言默认「设备」前缀） | 可选采纳；建议留空回落显示序列号（更简，YAGNI） |
| 编辑 | 行内改名：卡片上点击别名 → 输入框（`editValue = d.alias`），Enter 提交/Esc 取消，`value === d.alias` 不写盘；`renameDevice(id, alias)` 单入口；trim 归一（config.rs 102） | 直接照搬交互语义（本项目换 Element Plus 载体） |
| 展示 | 卡片主标题即别名，错误/确认文案一律 `alias || deviceId`（DevicePage.svelte 247/726） | 采纳：列表/标题处「别名优先、序列号兜底」 |
| 持久化卫生 | 校验失败不改配置；`connectionId` 探测补全（USB 序列号与 ip:port 双向迁移归并） | 本项目 DeviceId 语义已统一（`ip:port` / 序列号），无连接标识漂移问题，不吸收 |

## 二、本项目现状与落点选型

现状链路（实证）：设备列表 `GET /api/devices` → `DeviceService.list_devices()`
返回 `DeviceInfo`（domain/device.py，`slow` 为既有瞬态字段先例）；Dashboard
为 el-table（列 `id/model/...`，**无搜索框**）；方案 45 已有
`config/settings.py` `devices: dict[DeviceId, DeviceOverride]` 设备级节 +
`PUT /api/settings` 合并写 user.yaml（`interfaces/http/settings.py`
`_merge_devices` 48-80）+ user.yaml 热重载 + 原子写。

**存储落点两案**：

- **A（推荐）：user.yaml `devices.<id>` 节加 `alias` 字段**。复用方案 45
  整条链：DeviceId 键白名单校验、`_merge_devices` 合并、原子写、热重载、
  `GET /api/settings` 自动带出（`model_dump(exclude_none=True)`）；不碰
  SQLite；别名随 serial/ip:port 键天然跨连接持久。代价：devices 节语义
  从「参数覆盖」扩为「设备级用户配置」（可接受，节定位本即用户级设备配置）。
- **B：SQLite devices 表加列**。否决——该表是 ADB 快照缓存，`save()` 用
  `INSERT OR REPLACE` 整行覆盖（sqlite.py 607），用户字段会被下次刷新
  洗掉；改 `ON CONFLICT DO UPDATE` 保留列 + schema 迁移 + Repository 端口
  扩签名，侵入大且表本身不可靠（slow 字段即证）。

## 三、方案设计（拍板后实施）

### T1 后端：alias 读写链

- `config/settings.py`：`DeviceOverride` 增
  `alias: str | None = Field(default=None, max_length=64)`（限长防 user.yaml
  膨胀；中文/emoji 允许，trim 归一在写入端做）。
- `interfaces/http/settings.py` `_merge_devices`：section 循环保留
  stream/recording，另处理 `alias`——提交非空字符串 → 覆盖写（trim）；
  提交空串 → `pop("alias")` 清除；整设备传 null 删除语义不变。
- `DeviceInfo`（domain/device.py）增瞬态字段 `alias: str = ""`（同 `slow`
  先例：不入库、仅呈现）；`DeviceService.list_devices()` 返回前以
  `settings().devices[id].alias` 填充；`GET /api/devices/{id}` 同口径。
- SSE `connected` 事件 payload（interfaces/http/devices.py 96-106 手工构
  字段）补 `alias` 一键。
- 写入口**不校验设备在线**：复用 `PUT /api/settings`，语义是设备级配置；
  UI 入口只在连接设备卡片上提供（对齐用户「连接了能添加」诉求而不加后端
  耦合）。

### T2 前端：展示 + 编辑 + 搜索

- Dashboard 设备表：ID 列改「备注/设备ID」复合显示（alias 加粗主行、
  序列号副行；无 alias 仅序列号，与 FreeControl2「alias || deviceId」一致）。
- 行内编辑：操作列加「备注」按钮 → `ElMessageBox.prompt`（当前值预填、
  maxlength=64、确认 trim；空串=清除备注）→ `api.putSettings({ devices:
  { [id]: { alias } } })` → 成功后本地 store 该设备 alias 即时更新（不整表
  重拉）。
- 快速定位：表格上方加搜索框（Element Plus `el-input` 前缀搜索图标），
  按 备注/型号/设备ID 子串过滤（大小写不敏感）；空结果走既有空态样式。
- Settings 设备级覆盖卡：设备下拉项标题带 alias（若已拉 devices 列表则零
  额外请求）。

### T3 文档与手册

README 功能清单、docs/用户手册.md（设备管理节 + Q&A）补备注说明。

## 四、FreeControl2 增量借鉴扫掠（立项外，只列评估）

| 对方功能 | 本项目现状 | 评估 |
|----------|-----------|------|
| LaunchBar 命令预览/复制（command = 参数拼装所见即所得） | 服务端编排形态无命令行面；方案 45 已评估并以「生效参数回显+来源标注」对位 | 已覆盖，不重复借鉴 |
| 诊断页：会话输出进程退出后仍保留 + 2s 自动拉最新 | 已有 debug_sessions/debug_logs 表 + 设置页日志查看；scrcpy 子进程输出留存于日志 | 大体等价；「已结束会话输出原位可查」若后续有反馈再细化，进低优先池 |
| 优雅结束会话：先 `taskkill` 发关闭请求、超时兜底强杀，让 scrcpy `--cleanup` 还原设备 | 方案 48 D2 用设备侧 `kill -9` 治理残留（server 子进程由 WS 断开自然收尾） | 语义已被 48 覆盖（我们杀的是设备侧 server 进程，非本机窗口进程）；无需跟进 |
| 多设备窗口自动排列（--window-x/y 槽位矩阵） | Web 多路页面网格天然承担 | 不适用 |
| HelpTip 参数悬浮说明 | 方案 47 已做 /settings/help 参数手册页 | 等价达成；悬浮提示为锦上添花 → 低优先池 |
| 设备登记制 + 未验证确认框（UnverifiedConfirm） | 无登记制（本需求也不需要）；scrcpy-server 由随包分发无「未验证构建」问题 | 不适用 |
| 别名默认「设备N」 | 本方案留空回落序列号 | D2 拍板点，默认不采纳 |

**结论**：除别名机制（本方案主体）外，FreeControl2 其余可借鉴面已被方案
45/47/48 或本项目既有能力覆盖/判定不适用，无第二个体量级立项点。

## 五、拍板点（2026-10-16 用户按推荐全数拍板）

- **D1 存储落点**：✅ A——user.yaml devices 节。
- **D2 默认别名**：✅ 留空回落显示序列号（不采「设备N」）。
- **D3 搜索范围**：✅ 备注+型号+设备ID 三字段子串。
- **D4 备注展示位扩展**：✅ 本期仅 Dashboard 表列 + 设置页下拉标题；
  流页标题/录屏页/文件页标题带 alias 为后续可选（改动分散，不混入本期）。

## 六、验收（拍板后）

- 后端 pytest：DeviceOverride.alias 白名单/限长、_merge_devices 三种写
  语义（覆盖/清除/删设备节）、list_devices/SSE 带 alias；GET settings
  带出；user.yaml 热重载后列表备注即时刷新。
- 前端 vitest：prompt 编辑流（提交/取消/空串清除）、过滤逻辑、alias 优先
  渲染；门禁口径全量（ruff+mypy+pytest cov≥80+vitest+vue-tsc+eslint .）。
- 真机手工（.18/.25 任一）：连接 → 加备注 → 刷新/重连后仍在 → 搜索命中 →
  清除备注。
- exe 冒烟：并入下轮重打包清单。

## 七、实施分解（SDD，待排期）

- T1 后端 alias 读写链（含单测）。
- T2 前端展示/编辑/搜索（含 vitest）。
- T3 文档回写 + 收口门禁。
