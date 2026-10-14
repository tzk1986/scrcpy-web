# 方案 45：FreeControl2 对比与参数设置及运行环境优化方案

> **状态**：待 review（2026-10-14）——方案先行，实施待排期。
> **背景**：用户要求研究相似项目 [FreeControl2](https://github.com/pdone/FreeControl2)，进行功能对比，
> 重点比较「参数设置」「运行环境设置」两个维度，生成对应优化方案。

## 一、背景与目标

**用户意图**：以 FreeControl2 为参照系，评估 OpenScrcpy 在「参数设置」与「运行环境」
两个维度的优化空间，产出可 review 的优化方案。

**成功标准**：
- 双维度对比有据可查（对照两仓真实实现，非 README 宣传口径）；
- 优化建议分档明确（采纳 / 可选 / 不采纳 + 理由），任何一项可在 review 时单独拍板；
- 采纳项的落地设计达到「review 后可直接排期实施」的深度。

**分析前提（形态差异）**：FreeControl2 是 Rust + Tauri 桌面单机应用（本地 scrcpy.exe 全量参数
直控）；OpenScrcpy 是 Web 服务端（FastAPI 起 scrcpy-server.jar，浏览器多人访问）。
**参数面受两层限制**——server 端支持面 + 自身架构面（截图/流双链路、自适应码率、
方案 17 12B 包头协议等），不能也不应照搬「57 项全量面板」。

**约束**：零新依赖评估口径；不改动现有抓帧/流链路；本文档仅立项，实施另行排期。

## 二、FreeControl2 调研（2026-10-14）

### 2.1 项目概览

| 项 | 内容 |
|---|---|
| 技术栈 | Rust + Tauri 2 + Svelte + Vite + TypeScript，Windows 免安装单 exe |
| 数据落点 | `%APPDATA%/FreeControl2/config.json`（单一配置，含 `schemaVersion`） |
| 配置模型 | 设备清单（别名⇄连接标识映射）+ `defaults`（8 组参数）+ `overrides`（设备级覆盖）+ `channels`（下载通道）+ `logging`（日志保留） |
| 存储哲学 | 前端拥有字段模型（758 行 `params.ts`），后端以 JSON 透明存储（前向兼容，老配置可读新字段可缺） |

### 2.2 参数设置体系（重点）

**数据驱动字段模型**（`src/lib/params.ts`，758 行）：

| 组 | 键 | 代表字段 |
|---|---|---|
| 显示源 | `source` | `--video-source`（主屏/摄像头）、`--new-display` 虚拟显示器、摄像头方向/尺寸/缩放 |
| 视频 | `video` | `--video-codec`（h264/h265/av1/vp8/vp9）、`--max-size`、`--video-bit-rate`、`--max-fps`、`--crop`、`--angle`、`--render-fit`、`--video-buffer` |
| 音频 | `audio` | `--no-audio`（默认 true）、`--audio-source`、`--audio-codec`、`--audio-bit-rate`、`--audio-dup` |
| 控制 | `control` | `--no-control`、键鼠/手柄开关、`--show-touches`、`--turn-screen-off`、`--stay-awake`、剪贴板同步 |
| 录制 | `record` | `--record`、路径/格式/方向、`--no-window`、`--time-limit`、`--pause-on-exit` |
| 窗口 | `window` | 标题/尺寸/位置/外观（桌面特性） |
| 应用 | `apps` | `--start-app` |
| 高级 | `advanced` | verbosity/cleanup、`port`/`tunnelHost`、**自定义参数透传**（原始 flag 直通） |

- 控件类型：`toggle/segmented/select/text/number/range/chips/textarea`；
  字段带 `hint`/`unit` 展示语义、`showWhen` 条件显隐（字段级 + 卡片级）；
- **出厂原则「不替用户预设」**（`defaults.ts`）：空串/0 不产生 flag，交给 scrcpy 自身默认值；
  唯一例外 `--no-audio` 默认 true（部分机型拒绝 AudioRecord 会开会话即退，属实证兜底）；
- **设备级参数覆盖**：`OverrideEntry { enabled, values }` 持久化，单设备一套参数可整体启停、
  禁用不清值；
- **命令预览**：面板底部拼出完整命令行，一键复制；
- 设备清单：别名映射连接标识（USB→serial / WiFi→`IP:端口`），派生 `connectionId` 单源算法。

### 2.3 运行环境体系（重点）

| 机制 | 实现（`src-tauri/src/download.rs` 3947 行 + `EnvironmentPage.svelte`） |
|---|---|
| 获取方式 | scrcpy + adb **运行时按需下载**到应用数据目录（随包不含二进制） |
| 版本入口 | 「下载最佳适配版本」（构建期锁定）+「下载最新版本」（GitHub Releases latest）；`check_update` 四态（upToDate / updateAvailable / installedNewer / unknown） |
| 下载通道 | **直连 + 代理地址列表按序回退**；出厂两条代理为配置文件初始内容（可删不恢复）；失败自动降级、可手动「切换通道」；进度块标注当前通道/尝试 N/M |
| 官方校验 | 下载完成后比对官方 `SHA256SUMS`；**取不到校验值 → `needsConfirmation` 用户确认后才继续**（复用已下载文件，不重复下载） |
| 原子替换 | staging 目录下载解压（`StagedGuard` Drop 清理）→ 文件占用检测 → 释放随包 adb server → 会话停止检查 → `replace_dir` 原子替换；旧目录零残留 |
| 进度反馈 | `phase`（download/verify/extract/replace/done）+ 百分比 + 通道标签 + 已耗时；可取消、可切换通道 |
| 日志保留 | `7d / 30d / forever` 自描述字符串（不用 `0=永久` 类约定；未知取值退化出厂默认） |

### 2.4 其他设计速记

- 设备清单校验：USB 允许 serial 留空（待探测补全）；WiFi 必须 IP；端口缺省 5555 规范化；
- `schemaVersion` 迁移：缺字段按 0 读入再补成当前版本，手工编辑不炸；
- 首启缺失引导只做「告知 + 转场」，获取流程单点收敛在运行环境页（不存在双处状态不一致）。

## 三、双维度对比

### 3.1 参数设置对比

| 维度 | OpenScrcpy 现状 | FreeControl2 做法 | 差距评估 |
|---|---|---|---|
| 参数暴露面 | `config/base.yaml`：`stream` 5 项 + `recording` 6 项 + debug/metrics 若干；**手改 yaml + 热重载生效** | 8 组 ~57 项图形面板（数据驱动） | **大**——OpenScrcpy 无任何图形调参入口 |
| 面向人群 | 管理员（改 yaml） | 终端用户（面板） | 定位不同，但 Web 形态下用户即管理员，可兼得 |
| 生效单元 | 全局单源（yaml 一份） | 全局 defaults + 设备级 overrides | 中大——设备级覆盖缺失 |
| 参数可见性 | 无「当前生效值」查看界面（需读 yaml） | 面板即当前值 + **命令预览** | 中——可做「生效参数 + 来源」只读面板 |
| 默认值哲学 | 明确默认（base.yaml 全量给出） | 「不替用户预设」（空值不发 flag） | 定位不同；OpenScrcpy 的服务端默认值更适合「显式标注默认」 |
| 自定义参数 | 无（改 yaml 等价） | `customArgs` 原始透传 | 形态差异大，见 4.3 |
| 配置存储 | yaml（pyyaml，含大量注释文档） | JSON（透明存储，无注释） | yaml 注释是文档资产；写回须不伤 base.yaml |
| 参数变更生效 | 热重载（mtime 轮询），新流用新值 | 下次连接 | 语义可比，详见 5.3 |

**OpenScrcpy 已有而 FreeControl2 没有的**（不因对比而弱化）：
自适应码率（`adaptive_bitrate` + 档位阶梯重启编码器）、截图/流双链路、PTS 全链、调试面板
（logcat/shell/perf/网络）等——对比聚焦参数面，不做全功能优劣结论。

### 3.2 运行环境对比

| 维度 | OpenScrcpy 现状 | FreeControl2 做法 | 差距评估 |
|---|---|---|---|
| 二进制获取 | **随包绿色版**（adb 三件套 + jar，`fetch_tools.py` 构建期下载 + SHA256 锁定） | 运行时按需下载 | 定位不同——随包零网络是特性不是缺陷 |
| 版本可见性 | 用户不可见（jar v5.0.1 藏在包内；方案 44 已版本单源） | 环境卡片：已装版本 + 兼容性方向 + 目录 | **中**——信息透明化成本低、收益明确 |
| 更新通道 | 方案 44 换包 SOP（手动替换 jar + 常量，含 `.bak` 防误恢复） | 检查更新 + 一键下载替换（最佳适配/最新双入口） | 大改造成本；评估见 4.2/4.3 |
| 下载通道降级 | 无（无运行时下载需求） | 直连 + 代理按序回退 + 切换 | 不适用（无运行时下载则无对象） |
| 校验 | 构建期 SHA256 锁定（`fetch_tools.py` 清单） | 下载时官方 SHA256SUMS 比对；取不到需用户确认 | 随包模式下构建期锁定已足够 |
| 原子性 | 构建期流程内保证 | staging + 原子替换 + 占用检测 + adb 释放 | 随包模式无此问题 |
| 失败引导 | 无（二进制在包内，不存在缺失） | 未就绪引导 + 手动放置 + 重新检测 | 部分可借鉴（见 4.1-③） |

**结论**：运行环境维度 FreeControl2 的整套下载体系是「无随包二进制」前提下的自补方案；
OpenScrcpy 绿色版定位下**主体不适用**，但「版本/路径信息可见」与「检查更新提示」两个
轻量项有独立价值。

## 四、优化建议分档

### 4.1 P1·建议采纳

**① 运行参数设置前端化（新「设置」页 / 或设备页入口，范围待拍板）**

- 第一版范围建议（保守）：
  - `stream`：`max_size`、`bit_rate`、`fps`（+ `adaptive_bitrate` 开关）；
    `codec` 现状硬编码 h264（`stream/scrcpy.py:223`），暴露前须先放开实现，可列为第二版；
  - `recording`：`bit_rate`、`time_limit`（+ `retention_days`/`max_total_mb` 只读展示或可调待定）；
- 控件直接复用表单语义（number/select/toggle），不做 FreeControl2 式 8 组全量面板——
  debug/metrics 等调试参数维持 yaml（管理面）。

**②「当前生效参数」只读面板（含来源标注）**

- 展示 stream/recording 当前生效值 + 来源（出厂默认 / 用户设置 / 环境覆盖）；
- 这是 FreeControl2「命令预览」在 Web 服务端形态下的对位物：用户不直接看命令行，
  但需要「现在实际跑的是什么值、我从哪里改」。

**③ 运行环境信息透明化**

- 设置页增加「运行环境」信息区：scrcpy-server 版本（v5.0.1，方案 44 单源常量直接读）、
  adb 版本（`adb --version` 探测）、二进制路径（源码运行 tools/；frozen 为 `_internal/tools/`）；
- 只读展示，不改动二进制获取方式。

### 4.2 P2·可选（收益明确但非刚需）

- **检查更新（仅提示，不自动下载）**：手动点击 → 查 GitHub Releases latest → 对比本地 jar
  版本 → 提示「上游有 vX.Y.Z，升级走方案 44 SOP」；默认不联网、离线内网环境静默失败。
  需评估：服务端主动外联的网络合规性（用户环境以局域网为主）。
- **设备级参数覆盖**：per-device 覆盖 stream 参数（弱设备降规格），存储与写入路径同 5.1。
  收益中等（多设备场景）；等 5.1 落地后顺势扩展成本低。
- **截图模式专用参数**：截图链路现有 `max_size=0` 强制不缩放设计（`stream/scrcpy.py:125`），
  不动；若后续有片段预览清晰度诉求再议。

### 4.3 不采纳与理由

| 项 | 不采纳理由 |
|---|---|
| 运行时下载/替换运行环境 | 与绿色版定位冲突（随包即零网络、零安装）；已有方案 44 换包通道且构建期 SHA256 锁定——引入运行时下载 = 失去锁定 + 新增攻击面（下载链是新的不可信输入） |
| 下载通道/代理列表 | 依赖运行时下载场景，无对象；且 Web 服务端配置代理属部署面（应走 yaml/env） |
| `customArgs` 自定义参数透传 | **安全边界**：Web 服务端多人访问，浏览器可控字符串拼进服务端进程命令行 = 命令注入面；管理面诉求改 yaml 已满足（仅本地可信者可达） |
| 「不替用户预设」出厂哲学 | 语义不同：FreeControl2 是「用户本地单机、参数即用户意图」；OpenScrcpy 服务端默认值是**产品默认**，须显式（自适应码率等联动依赖 `bit_rate` 起始档） |
| 窗口/外观/快捷键类参数 | 桌面特性，Web 无载荷 |
| 音频输出/窗口类参数 | scrcpy-server 命令中 `audio=false` 硬编码（服务端无音频链路），暴露无意义 |

### 4.4 待拍板项（review 时定）

1. **P1-① 的载体**：独立「设置」页（导航挂新入口）vs 设备页内折叠面板 vs 全局对话框；
2. **P1 范围**：codec 暴露是否进第一版（涉及 `scrcpy.py` 硬编码放开 + 前端解码支持面核查）；
3. **P2「检查更新」**是否立项（网络合规考量）；
4. **设备级覆盖**是否立项（P2 排期）。

## 五、P1 落地设计草案

### 5.1 配置分层：`user.yaml` 用户覆盖层（基建已存在，延伸成本低）

**现状**（已核实）：`get_settings()` 已支持 `base.yaml` + `{env}.yaml`（`APP_ENV`）多文件
deep_merge；`config_watch` 按 mtime 轮询 `watched_paths()` 热重载。

**设计**：新增固定用户层 `config/user.yaml`（文件不存在 = 无覆盖）：

```
加载链（优先级低→高）：
  base.yaml（出厂默认，只读——注释是文档资产，永不写入）
  → user.yaml（用户设置，前端写接口的唯一目标）
  → {env}.yaml（部署环境层，既有语义不变）
  → 环境变量（BACKEND_PORT / DB_PATH 等既有覆盖，不变）
```

- `get_settings`：base 后固定合并 `user.yaml`（存在才合；解析失败 → 抛异常走既有
  「reload 失败保旧配置」语义，与 base 保护对称）；
- `watched_paths()`：**无条件**加入 `user.yaml`（缺失记 mtime 0.0，首次创建即触发重载；
  现有 env 文件逻辑只在 exists 时加入，首次创建侦测不到——此处不能照抄）；
- frozen 模式（exe 绿色版）：写向 `config_base_dir()` 同源目录（exe 同级 `config/`，
  不可写时回落已有逻辑）——与 config_watch 监听目录自然一致。

### 5.2 写接口与校验

- `PUT /api/settings`（或 `PATCH`）：体为 `{ stream: {...}, recording: {...} }` 子集；
- **白名单 + 类型 + 值域校验**（pydantic 模型 per section）：拒绝未知键、拒绝越界值
  （如 `max_size` 范围、`bit_rate` 格式 `NM/NK` 正则）；
- 写入方式：读 `user.yaml`（缺失视为 `{}`）→ deep_merge 提交子集 → `yaml.safe_dump`
  全量写回（user.yaml 是机器文件，无注释担忧）→ mtime 变化 → config_watch 自动重载；
  写接口自身不调 `reload_settings`（避免双通道竞态，统一走 watcher）；
- 响应回读：`GET /api/settings` 返回「生效值 + 来源」合并视图（供 4.1-② 面板）。

### 5.3 生效语义（写进 UI 提示）

| 参数 | 生效时机 | 交互提示 |
|---|---|---|
| `stream.*` | 对**新启动的流**生效（现流保持旧参数跑，重启设备页或重连即用新值） | 保存后 toast「已保存，下次连接生效」 |
| `recording.*` | **立即生效**（start 时读 `settings()`，每次录屏即当前值） | toast「已保存」 |
| `codec`（若第一版纳入） | 同 stream；需核实前端 WebCodecs 对 h265/av1 的 `VideoDecoder` 支持面（Chrome 版本相关） | 需先补支持面核查任务 |

> 实施时确认项：`adaptive_bitrate` 的 `bitrate_tiers` 起始档取「不超过 `bit_rate` 的最大档」——
> 若用户把 `bit_rate` 改小，档位联动是否仍合理（现逻辑天然覆盖，仅回归测试确认）。

### 5.4 前端页面形态（草案，对应 4.4-①）

- 顶部导航（现有布局）+「设置」入口；页面两卡片：
  - 「运行参数」：P1-① 范围表单 + 保存/重置为该节出厂默认；
  - 「运行环境」：P1-③ 只读信息区（版本/路径）+ 「生效参数」只读表（来源标注）。
- 形态最终以 4.4-① 拍板为准。

## 六、任务分解（拟，实施待排期）

> 以下为 P1 采纳后的实施序列草案；P2 项经拍板后另行拆解。

1. **T1 配置分层**：`user.yaml` 加载链 + `watched_paths` 无条件监听的单测
   （含：首次创建触发重载、解析失败保旧、env 顺序回归）；
2. **T2 设置读写接口**：`GET/PUT /api/settings` + pydantic 白名单校验 + 单测
   （未知键 400 / 越界 400 / 合并正确性 / 来源标注正确性）；
3. **T3 设置页（运行参数）**：表单 + 保存 + 生效提示 + 重置默认 + vitest；
4. **T4 生效参数面板 + 运行环境信息区**：只读展示（版本探测后端聚合）；
5. **T5 文档**：用户手册「设置页」章节 + README + 经验记录（若出坑）。

**门禁**：沿用标准四件套（ruff + mypy strict + pytest cov≥80 + vitest/vue-tsc）；
零新依赖（yaml 读写用既有 pyyaml）。

## 七、风险与限制

1. **双写竞态**：用户手改 `user.yaml` 与前端 PUT 同时发生——以「后写覆盖」语义接受
   （同文件级 last-write-wins，无需锁；mtime 轮询自然收敛）；
2. **值域与联动**：`bit_rate` 档位、`max_size` 与设备物理分辨率的关系（截图链路强制
   `max_size=0` 不缩放）已在文档与 UI hint 注明；避免用户以为改 `max_size` 会改截图清晰度；
3. **frozen 只读回落**：Program Files 安装场景 `config/` 不可写 → 写接口返回明确错误
   （不静默失败）；绿色基本盘（exe 同级）不受影响；
4. **来源标注的准确性**：来源判定在后端合并时记录（而非前端猜测），避免「yaml 手改后
   前端标注过期」；
5. P2「检查更新」的外联合规需用户在拍板时明确。

## 八、附：FreeControl2 实现的值得记的细节

- `params.ts` 数据驱动 + 后端 JSON 透明存储 = 前端字段模型演进不炸老配置（前向兼容）；
- 下载「最佳适配（构建期锁定）」与「最新（上游 latest）」双入口分离——**不把「最新」当默认**；
- 未取到官方校验值时不静默继续（`needsConfirmation`），也不静默放弃（复用已下载文件）；
- `StagedGuard`（Rust Drop 清理 staging）+ 文件占用检测 + adb server 释放 + 会话停止检查——
  替换一枚二进制在 Windows 上的完整防御清单；
- 日志保留用自描述字符串（`7d/30d/forever`）而非魔数约定；未知值退化默认而非清空。