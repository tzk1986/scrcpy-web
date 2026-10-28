# 方案 53：exe 调试终端关闭后标准句柄投毒治理

> 状态：定稿待实施　|　发现版本：v0.7.15 发布包　|　日期：2026-10-28

## 1. 症状

exe（frozen 3.10.11）打开调试终端并关闭后，**进程内一切 asyncio/subprocess spawn
永久报 `[WinError 6] 句柄无效`**：设备列表 500（`device_refresh_failed` 每 5s 刷
error 日志）、连接设备、自动上下线全部失效。仅重启 exe 恢复。

## 2. 证据链（已实锤：PEB 标准句柄悬垂）

外部探针 `tests/manual/probe_53_peb_handles.py`（OpenProcess+ReadProcessMemory 读
PEB→ProcessParameters+0x28/0x30/0x38，跨进程 DuplicateHandle 验生死）：

| 进程 | stdin | stdout | 判读 |
|---|---|---|---|
| 投毒用户实例 36756 | 0xb40 **DEAD(err=6)** | 0x224 **DEAD(err=6)** | PEB 值保留、句柄表项已关 → 悬垂实锤 |
| 健康对照 46988 | 0xe8 ALIVE | 0xec ALIVE | 正常 |

- stderr 两侧均 DEAD = 控制台伪句柄跨进程 dup 失败的**探针伪影**，非证据。
- 探针曾把 GetCurrentProcess 伪句柄按 c_int 截断传参导致首轮全 DEAD，已修
  （`ctypes.c_void_p(-1)`），判读以修正后为准。
- 偏移自校验：PEB+0x10 ImageBase 与 Toolhelp 首模块一致；params+0x00 对为
  MaximumLength/Length，结构判读正确。

### 失败机制（消费端）

Python 3.10 `subprocess._get_handles`：`stdin=None` 分支
`GetStdHandle(STD_INPUT_HANDLE)` 返回 PEB 旧值（悬垂）→ `_make_inheritable`
DuplicateHandle → **WinError 6，永久**。backend 30 个 spawn 点中 29 个未显式传
stdin，全部踩在该分支上；少数点（scrcpy.py 输入 tap/swipe/key/text 5 处）连
stdout/stderr 也继承，投毒实例 stdout 同样悬垂。

### 投毒端（凶手定位，进行中）

CloseHandle 是进程本地操作 → 凶手必在投毒进程内 native 路径：
- 已排除：backend 全部 Python 代码（无 CloseHandle/SetStdHandle/GetStdHandle/
  FreeConsole/os.close）、3.10 stdlib 继承分支（有 `p2cwrite != -1` 守卫）、
  launcher。
- 头号嫌疑：pywinpty 3.1.1 = Rust winpty-rs 1.0.7 ConPTY 后端——构造时
  `AllocConsole()`（GUI 进程成功），teardown 路径（Drop L660-684 / 子进程退出
  cleanup 线程 L70-74）调用 `ClosePseudoConsole + FreeConsole`；FreeConsole 关闭
  进程 std 句柄表项但 PEB 旧值保留 = 恰好是观测到的投毒签名。
- 未复现项（控制变量矩阵全部干净）：
  - spike C：pythonw+真 PIPE 句柄下 raw AllocConsole/FreeConsole 循环、完整
    spawn_conpty→teardown（观察：Drop 未跑 FreeConsole，console 泄漏）；
  - spike D：dev 8766 黑盒视频+调试组合；
  - spike E ×2：**exe 第二实例、视频真实出流 40 帧（.19）+调试开关+PEB 逐步
    探针**，全程 stdin/stdout ALIVE，NOT REPRODUCED。
  - 注：前几轮视频从未真正出流（.18 有同网段客户端 10.50.10.6 持续重启
    scrcpy 会话触发残留守卫），E run#3 换 .19 后补齐该要素仍未复现。
- 剩余差异假设：tessoa 拉起拓扑、多次开关/多会话竞态、cleanup 线程与
  taskkill 竞态等。触发点定位列为后续观察项（复发时现场探针即可钉死），
  不阻塞治理——治理与具体触发点无关。

## 3. 方案对比与拍板

| 方案 | 内容 | 效果 | 代价 |
|---|---|---|---|
| A 全面显式 stdin（拍板采纳） | 30 个 spawn 点全部显式传 `stdin=DEVNULL`（交互 shell 保持 PIPE），5 个无捕获输入点补 stdout/stderr=DEVNULL | 彻底绕开继承分支，投毒后 spawn 不再爆 | ~30 处机械修改 |
| B 运行时自愈（拍板采纳） | 新增 `std_guard.repair_std_handles()`：三个 std 索引 GetStdHandle→非 0 且 DuplicateHandle 失败→CreateFile("NUL")→SetStdHandle 替换。挂接 `device_refresh_failed` except 分支（含 WinError 6 时修复，下一 tick 自然恢复） | 免重启自愈；即使未来新代码漏传 stdin 也能兜住 | 一个小工具模块 + 1 处挂接 |
| C 继续追 native 触发点 | 复发时探针钉死，评估替换 pywinpty 后端/升级 | 根治投毒源 | 不确定性高、依赖上游 |

A+B 组合：A 消除现有暴露面，B 兜住残余与未来暴露面并消灭"必须重启"。
C 作为复发观察项挂起。

## 4. 实施清单

1. `backend/app/infrastructure/adb/std_guard.py`（新建）：`repair_std_handles() -> list[str]`
   （返回修复项标签，非 Windows no-op；仅替换 DuplicateHandle 已死的悬垂句柄，
   活句柄绝不触碰）。
2. spawn 点补参：
   - cli.py L420/628/815/971/1017；server_manager.py L142/206/232/259/289/343；
     scrcpy.py L187/208/260/376/875/922/938/953/1017 → `stdin=asyncio.subprocess.DEVNULL`
   - scrcpy.py L815/825/835/843/851（tap/swipe/long_press/key/text）→ stdin 之外
     补 `stdout=DEVNULL, stderr=DEVNULL`
   - winpty.py L140 taskkill、system.py L87 adb version（`subprocess.run`）→
     `stdin=subprocess.DEVNULL`
   - shell.py L153 已有 stdin=PIPE，不动
3. `device_service.py` L594 except 分支：错误串含 `WinError 6` 时调用
   `repair_std_handles()` 并记 `std_handles_repaired` 日志。
4. 测试：
   - `tests/infrastructure/test_std_guard.py`：子进程内 CloseHandle 掉 STD_INPUT
     悬垂化→断言 repair 后 GetStdHandle dup ALIVE、spawn 不再 WinError 6
     （Windows-only，skip on other platforms）。
   - 现有 spawn 相关单测回归（mock 层不受影响）。
5. 手工验证：build/venv 起 exe 第二实例 + spike E 同款序列冒烟一遍
   （stdin=DEVNULL 全链路线上行为不变）。

## 5. 风险

- DEVNULL 化的 stdin 对 adb shell/exec-out 无功能影响（从不写 stdin）；
  交互调试 shell 保留 PIPE。
- SetStdHandle 只替换已悬垂句柄，不影响 ConPTY 自身管道与 pywinpty 内部逻辑。
- 若上游 pywinpty 竞态根因后续定位，B 层自愈可降级为纯保险。

## 6. 发布

防御性补丁，建议随 **v0.7.16** 补丁版发布（发布需单独授权）。
