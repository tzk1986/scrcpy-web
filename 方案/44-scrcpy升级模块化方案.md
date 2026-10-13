# 方案 44：scrcpy 升级模块化方案

> **状态**：已实施（2026-10-13）——T1-T5 全链落地 + v5.0.1 真实升级试跑通过（真机出流/控制/`.bak` 拦截）；实施记录见第七节。
> **背景**：立项时随包 scrcpy-server v4.1（上游最新 v5.0.1，2026-10-08）——2026-10-13 已按本方案升级至 v5.0.1；
> 用户要求评估「上游更新后仅换包即升级」的可行性，并给出升级方案，
> 方便后续升级使用。

## 一、背景与目标

**用户意图**：scrcpy 上游迭代较快，希望后续升级成本可控——理想形态
「仅替换随包 jar + 少量常量」即可完成升级，而不是散点改代码。

**成功标准**：
- 全仓 scrcpy-server 版本引用**单源化**（除 jar 本身外，版本只在 1 处
  定义，其余全部推导/引用）；
- 清理现存散点与 2.4 残留（上次升级漏改的 4 处为证）；
- 升级 SOP 文档化（照做即可，含校验清单）；自动化校验补强（启动参数
  与常量一致性、jar 哈希与清单一致性）；防升级误恢复加固（.bak 机制）；
- （已实施）以 v5.0.1 完成一次真实升级试跑，实证升级通道（真机出流/控制已验；
  exe 打包核验随 0.7.6 顺延，见 §七 遗留）。

**约束**：不改变 scrcpy 协议代码（除非升级目标跨大版本）；零新依赖；
不动抓帧/流链路；master 直做、禁 push。

## 二、现状调研（2026-10-13）

### 耦合点清单（版本升级必须同步的位置）

| # | 位置 | 内容 | 性质 |
|---|---|---|---|
| 1 | `backend/app/scrcpy/scrcpy-server.jar` | 二进制本体 | 替换 |
| 2 | `backend/app/scrcpy/constants.py:127` | `SCRCPY_SERVER_VERSION = "4.1"`（全仓唯一生效常量） | 必改 |
| 3 | `backend/app/infrastructure/stream/scrcpy.py:218` | app_process 首参取常量（server 端硬校验：版本不符即拒启 "does not match"） | 消费 |
| 4 | `backend/app/scrcpy/server_manager.py:301` | 以常量充当 get_server_version（无设备端探测） | 消费 |
| 5 | `scripts/fetch_tools.py:41,90` | JAR_URL 硬编码 `v4.1` + SHA256 手抄 | 必改 |
| 6 | `tests/unit/test_fetch_tools.py:45-47` | URL 尾缀断言 v4.1 | 必改 |
| 7 | `tests/scrcpy/test_constants.py:183` | `== "4.1"` 断言 | 必改 |
| 8a | 已随 4.1 更新（散点）：`tests/scrcpy/debug_scrcpy_server.py:67`、`debug_scrcpy_minimal.py:72`、`tests/manual/probe_encoder_stability.py:122`、`spike_stream_reuse.py:37` | 脚本内硬编码 4.1 | 散点 |
| 8b | **仍是 2.4（上次升级漏改）**：`tests/scrcpy/test_scrcpy_manual.sh:18`、`test_scrcpy_minimal.sh:4`、`debug_scrcpy_direct.py:51`、`backend/app/scrcpy/README.md:213` | 残留 | 散点/腐化 |
| 9 | 打包 `openscrcpy.spec:47` | 按目录注入 jar（无版本耦合） | 无耦合 |
| 10 | 前端/文档 | 无版本显示（文档仅注释说明） | 无耦合 |

> **实施期纠正（T6）**：第 4 行路径立项时误记于 `backend/app/infrastructure/stream/`——实际在
> `backend/app/scrcpy/server_manager.py`（`get_server_version` 返回常量；`.bak` 恢复判据在
> 同文件 `:206`，二者均为立项时点行号）。

### 协议耦合（仅跨大版本才动）

- 启动参数表（`scrcpy.py:213-236`：max_size/max_fps/video_bit_rate/
  tunnel_forward/control/audio/send_frame_meta/raw_stream 等）——对照
  v5.0.1 `Options.java` 键名全部仍存在；
- 握手（`stream_protocol.py:161-170`：1B dummy + 64B 设备名 + 4B codec id）；
  12B 包头（**v4.0 新引入的 session 包**）；
- 控制消息（`control_sender.py` 全部：touch 32B / keycode / scroll /
  RESET_VIDEO=17）——逐字节对照 v5.0.1 `ControlMessage.java` 一致。

### 上游演进与兼容性（WebSearch，2026-10 时点）

- 版本线：3.0（2024-11-24，server 57 文件变更）→ 4.0（05-12，断层：server
  48 文件变更 + 12B session 元数据）→ 4.1（07-12，VP8/VP9）→ **5.0.1
  （2026-10-08，最新）**；
- **4.1 → 5.0.1：server/ 仅 4 个文件变化**（构建脚本、音频、相机），
  `Options.java` / `Streamer.java` / `ControlMessage.java` **字节级一致**；
  5.0 的硬件解码亮点全在官方 client 侧，与本自研客户端无关；
- 上游官方立场（doc/develop.md）：**server 与 client 版本必须完全一致**
  （jar 内 VERSION_NAME 硬校验，不符即拒启）；协议视为内部实现，**无向前/
  向后兼容承诺**。

### 结论：分档

| 档 | 可行性 | 说明 |
|---|---|---|
| 同协议代（4.x→5.x，协议未变） | **接近「仅换包」**：换 jar + 常量 + URL/SHA + 2 处测试断言 ≈ 完成 | 散点单源化后即成标准 SOP |
| 跨大版本（如 3.x→4.x 式断层） | **否** | 协议代码（握手/包头/控制消息/参数）必须逐项重核重写；本方案不为其设计适配层，届时应单独立项 |

### 附带发现（风险点）

- **`.bak` 误恢复**：server_manager 对新旧 jar 仅按文件大小比对来恢复备份
  （`server_manager.py:206`）——升级后新旧 jar 同尺寸会错误恢复旧版；
- 官方 jar 有 `SHA256SUMS.txt`（含 GPG 签名）可双源校验，替代目前手抄 hash。

## 三、设计

### §1 版本单源化

- `fetch_tools.py` 的 JAR_URL 改为由 `SCRCPY_SERVER_VERSION` 模板推导
  （`https://github.com/Genymobile/scrcpy/releases/download/v{V}/scrcpy-server-v{V}`，
  官方资产无 `.jar` 后缀）；脚本独立运行需处理 import 路径（`sys.path`
  注入 backend 或共享纯常量数据文件，零新依赖）；
- 散点全清：8a/8b 全部改读常量（或环境变量兜底）；2.4 残留一次性归零；
- 测试断言改引用常量（断言变为「URL/参数与常量一致」而非钉死字符串——
  升级时只改常量一处）。

### §2 升级 SOP（文档化 + 校验自动化）

扩 `docs/经验记录.md`（E028 新增）+ 本方案附录：
1. 改 `SCRCPY_SERVER_VERSION`（若上游换 URL 模板一并改 fetch_tools）；
2. `python scripts/fetch_tools.py` 下载 + `--print-hash` 回填 + 对照官方
   `SHA256SUMS.txt` 双源核对；
3. 跑三处锁定测试（URL / 常量 / 启动参数一致性）；
4. 用 GitHub compare API 比对 server/ diff 与 release notes——**判定是否
   跨协议代**（若 `Options.java` / `ControlMessage.java` 有差异 → 进入协议
   重核模式，非本 SOP 范围）；
5. 真机 smoke（.25：推流 30s 出图 + 控制注入生效）；脚本捕获
   "does not match" 即判失败；
6. 打包核验（exe zip 内含新 jar 且哈希与清单一致）。

### §3 自动化校验补强

- 单测：启动命令 `args[0] == SCRCPY_SERVER_VERSION`（离线锁定，防消费点
  漂移）；
- 单测：fetch_tools 下载清单哈希与实际 jar 一致（`--verify` 完善，若既有
  能力覆盖则补测试锁定）；
- 可选：离线读 jar 内 VERSION_NAME 字符串与常量比对（实现方式实施时评估）。

### §4 .bak 误恢复加固

- server_manager 备份恢复判据从「文件大小」升级为「大小 + mtime/hash 之
  一」（同尺寸不同内容不再误恢复）；补单测覆盖「新旧同尺寸」场景。

### §5（已实施）v5.0.1 真实升级试跑

- 按 §2 SOP 把版本从 4.1 升到 5.0.1（协议已字节级核验无变化，预期风险
  低），完成通道实证：真机出流/控制 + exe 打包 + 冒烟；异常则回滚
  （改回常量 + 恢复旧 jar，git 历史留底）。
- 战略意义：以一次低风险真实升级，把「模块化升级通道」从文档变成被
  验证的能力。

## 四、任务分解（拟，实施待排期）

| 任务 | 内容 | 规模 |
|---|---|---|
| T1 | 版本单源化：fetch_tools 模板化 + 散点清理（含 2.4 残留）+ 测试断言改引用 | M |
| T2 | 自动化校验：启动参数一致性单测 + jar 哈希校验测试补强 | S |
| T3 | .bak 误恢复加固 + 单测 | S |
| T4 | （选做）v5.0.1 升级试跑：常量/URL/hash + 真机 smoke + exe 核验 | M |
| T5 | 文档：经验记录 E028 升级 SOP + README/发布说明版本引用核对 | S |

## 五、风险与已知限制

| 项 | 说明 | 缓解 |
|---|---|---|
| 升级试跑未知回归 | 协议已字节级核验一致，风险集中在构建差异 | 真机 smoke + 回滚路径（常量 + 旧 jar 在 git 历史）兜底 |
| 跨大版本升级 | 未来上游若再破坏协议，仍需协议重核 | 非本方案承诺；SOP 第 4 步负责识别并单独立项 |
| fetch_tools 独立运行 | 脚本导入 backend 常量需处理 PYTHONPATH | `sys.path` 注入或共享纯数据模块（零新依赖） |
| 官方 SHA256SUMS 获取 | 需随 releases 页下载 | SOP 步骤固定来源 URL；失败退回单源校验并记录 |

## 七、实施记录（2026-10-13）

> 按 SDD 计划分任务执行（`docs/superpowers/plans/2026-10-13-scrcpy-upgrade-modularization.md`；
> 实际拆分与 §四「拟」略有出入：散点清理独立成项、文档回写为末项——下表按执行序）。
> master 直做、未 push。v5.0.1 真实升级试跑通过——「仅换包即升级」通道从文档变为已验证能力。

| 执行序 | 提交 | 验证结论 |
|---|---|---|
| 计划 | `6ad551c` | 6 任务拆分定稿（含全局约束与 Review Focus） |
| ① 版本单源化核心 | `e737922` | `scrcpy_version.py` 唯一分发点（零依赖，脚本 runpy 读取）+ fetch_tools URL 模板推导；TDD RED→GREEN 37 passed |
| ② 散点清理 | `c46c1af` | debug/manual 脚本改读分发点，2.4 残留 4 处归零（含 scrcpy README 版本引用） |
| ③ 校验自动化 | `c99ef42` | 启动命令首参单测锁定（突变法验证测试有效性）+ `fetch_tools --verify-local` 离线复核 |
| ④ `.bak` 恢复加固 | `ff2f2aa` | 大小列解析 + SHA256 双判据，同尺寸不同内容拒恢复；RED 复现旧子串陋判、GREEN 类内 8 passed |
| ⑤ v5.0.1 升级试跑 | `3e43d2f` | 双源核实 + smoke A/B 全过（实测数据见下） |
| ⑥ 文档回写 | 本次收口提交 | E028 SOP + 本文档回写与纠错 + 方案索引 + 进度追踪第七十七次更新 |

（执行期间 `487fa02` 为路线图第 14 项「Android 11+ 无线调试配对支持」登记，与本方案无关。）

### v5.0.1 试跑实测数据

- jar SHA256 `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`
  （733930 B）：本地下载 = 官方 `SHA256SUMS.txt` v5.0.1 行，**双源一致**；
- `--verify-local` 四件全 OK；门禁 **1131 passed / 1 skipped / cov 93.23%**，
  ruff/mypy 零新增红——升级不使任何测试变红，单源化生效实证；
- **smoke A**（.25 `push_server` 直调）：`push_server: True`；设备端残留 4.1
  `.bak`（733706 B）被**大小判据**正确拦截（无 `restored_from_bak`）；设备端
  jar 经 `exec-out` + 本地 sha256sum 字节级 = 官方 v5.0.1；
- **smoke B**（.25 `probe_encoder_stability.py --seconds 60 --reset-at 30`）：
  出流全程持续、无 "does not match"；RESET_VIDEO 经控制链签收（服务端日志
  `Video capture reset`），重置后 SESSION/CONFIG 重建、key 帧延迟 0.22s；
- 补充：probe 结束主 jar 退出自删（E002）后仅剩新版 `.bak`，再调 push →
  `scrcpy_server_restored_from_bak` + True——升级后常态恢复路径双向闭环。

### 实施期发现、纠错与决策

1. **§二 #4 路径误记**（本文档已更正）：立项时误记于 `infrastructure/stream/`，
   实际为 `backend/app/scrcpy/server_manager.py`（`get_server_version` 返回
   常量；`.bak` 恢复判据同文件）；
2. **SOP 条目编号纠错**：立项拟编 E015，但 E015 已被 v2.4→v4.1 一次性升级
   记录占用 → 实际编号 **E028**（§三 §2 与 §四 T5 相应更正）；
3. **存量 v4.1 措辞两类处置（评审 Major 修复）**：a) 注释/文档性质的历史
   记录——T5 报告 8 处 + 评审补漏 5 处（`tests/scrcpy/test_scrcpy_encoder.py:486,940`、
   `docs/API.md:754,765`、`docs/经验记录.md:828`）共 13 处（10 文件；该清单系
   两轮明确列出的点位、非全仓穷举口径；`docs/经验记录.md:829` 等同块相邻行
   同此处置），**保留真实性不逐处修改**；b) 现态引用——`docs/架构.md` 6 处
   （`:47`/`:150`/`:152`/`:156`/`:219`/`:392`；评审 sweep 检出、T5 grep 面
   未覆盖）已逐处核对并同步：`:150` 现行常量值改为版本无关表述（重导出 +
   唯一分发点 `backend/app/scrcpy/scrcpy_version.py`），其余 5 处随包版本/
   协议措辞更新为 v5.0.1；后续升级以 E028 为现行指引（其「参考/关联」已说明
   与 E015 的互补关系，覆盖 `docs/经验记录.md:828` 的过时指引）；
4. **设备端 sha256sum 可用性差异**：.25（Android 7.1.2 toybox）无 sha256sum →
   `.bak` 哈希判据自动降级仅大小判据（.18 有；.33 未验）——降级路径为真实
   需求而非假设，已记入 E028；
5. **门禁 PYTHONPATH 口径**：Git Bash 直跑 `PYTHONPATH=backend:.` 全绿依赖
   conftest sys.path 兜底（Windows Python 不以 `:` 作分隔符）；纯 Windows
   环境/严格口径用 `PYTHONPATH="backend;."`——已记入 E028。

### 遗留

- exe 打包核验不在本计划：0.7.6 打包时自然携带 v5.0.1 jar（届时核验 zip 内
  jar 哈希与清单一致；打包前仍须排查孤儿 adb 锁 / esbuild 锁两坑）；
- 跨大版本升级仍须协议重核（E028 第 8 步负责识别并单独立项）；
- push 远程：待用户显式授权。