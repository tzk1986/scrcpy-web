# dumpsys activity 真机样本 fixtures（方案 37 C1）

本目录存放 Task 13（C1）采集的三真机 `dumpsys activity` 原始输出，供后续解析任务
（C2/C3）的单元测试作 fixture 使用。

## 采集概况

- 采集日期：2026-10-12
- 采集环境：Windows 开发机，adb 36.0.0（PATH `/d/platform-tools/adb`）
- 计划三机 .18/.25/.33：**192.168.8.33 当次离线，按其在线替班机器 **192.168.8.19** 采集，
  文件名对齐实际 IP 尾号（`dumpsys_activity_19.txt`）。未创建 33 空壳文件。
- 归一处理：所有文件采集时经 `tr -d '\r'` 去除 CR，落盘为 UTF-8、LF 换行；
  落盘后逐个用 `open(f, encoding='utf-8').read()` 验证可解码，全部通过。
- services/providers 布局：`dumpsys activity services` 与 `dumpsys activity providers`
  的输出以注释头分隔追加在同设备主文件尾部：
  `# --- dumpsys activity services ---`、`# --- dumpsys activity providers ---`。
  C2 解析逐行锚定，混排无害。
- 「双来源」披露（C1 评审补正）：三机主输出均自带服务记录内嵌块，与追加
  services 段并存——.18 内嵌 34 / .25 内嵌 28 / .19 内嵌 18；C2 解析重复行
  照单全收，不做合并。

## 各文件明细

### dumpsys_activity_18.txt（11924 行）

- 设备：192.168.8.18:5555，型号 rk3568_r，Android 11
- 结构：主输出 1-10784（内嵌服务块 34 条 ServiceRecord）；services 段
  10785-11521（追加 34 条 ServiceRecord，双来源并存共 68 条）；
  providers 段 11522-11924（真实 provider 记录）
- 锚定行形态：仅 1 条 `mResumedActivity`；无 `topResumedActivity`/`mFocusedActivity`；
  另有 Android 11 形态的 `mFocusedApp=` 行（C2 需注意版本差异）

采集命令：

```bash
adb -s 192.168.8.18:5555 shell dumpsys activity | tr -d '\r' > dumpsys_activity_18.txt
printf '# --- dumpsys activity services ---\n' >> dumpsys_activity_18.txt
adb -s 192.168.8.18:5555 shell dumpsys activity services | tr -d '\r' >> dumpsys_activity_18.txt
printf '# --- dumpsys activity providers ---\n' >> dumpsys_activity_18.txt
adb -s 192.168.8.18:5555 shell dumpsys activity providers | tr -d '\r' >> dumpsys_activity_18.txt
```

### dumpsys_activity_25.txt（1670 行）

- 设备：192.168.8.25:5555，型号 rk3288，Android 7.1.2
- 结构：主输出 1-803（内嵌服务块 28 条 ServiceRecord）；services 段
  804-1398（追加 28 条 ServiceRecord，双来源并存共 56 条）；
  providers 段 1399-1670
- 锚定行：`mResumedActivity` 1 条（原 676 行）、`mFocusedActivity` 1 条（原 712 行）、
  无 `topResumedActivity`
- providers 输出形态：本项目曾预期 Android 7.1.2 的 `dumpsys activity providers`
  可能报错（子命令不支持），**本次采集实际成功返回完整 ContentProviderRecord 记录，
  未见任何报错**。该输出为真实样本，原样入库，未做修改。

采集命令：

```bash
adb -s 192.168.8.25:5555 shell dumpsys activity | tr -d '\r' > dumpsys_activity_25.txt
printf '# --- dumpsys activity services ---\n' >> dumpsys_activity_25.txt
adb -s 192.168.8.25:5555 shell dumpsys activity services | tr -d '\r' >> dumpsys_activity_25.txt
printf '# --- dumpsys activity providers ---\n' >> dumpsys_activity_25.txt
adb -s 192.168.8.25:5555 shell dumpsys activity providers | tr -d '\r' >> dumpsys_activity_25.txt
```

### dumpsys_activity_19.txt（1264 行，替班 .33）

- 设备：192.168.8.19:5555，型号 DWL801，Android 7.1.2（.33 离线，样本以在线 .19 替班采集）
- 结构：主输出 1-688；services 段 689-1036（18 条 ServiceRecord）；providers 段 1037-1264。
  另注意：主输出内部自带 `ACTIVITY MANAGER SERVICES (dumpsys activity services)` 块，
  含 18 条 ServiceRecord（原 482-553 行区间），与追加 services 段并存，C2 解析需一并兼容。
- 锚定行：`mResumedActivity` 1 条（原 588 行）、`mFocusedActivity` 1 条（原 609 行）、
  无 `topResumedActivity`
- 采集插曲（如实记录）：.19 的 adb transport 一度失活（shell 挂起、dumpsys 零输出、
  `dumpsys -l` 为空），`adb disconnect` / `adb connect` 后恢复，随后一次性完整采集。
  该插曲不影响样本完整性（三段落齐全、锚定行在位）。

采集命令：

```bash
adb -s 192.168.8.19:5555 shell dumpsys activity | tr -d '\r' > dumpsys_activity_19.txt
printf '# --- dumpsys activity services ---\n' >> dumpsys_activity_19.txt
adb -s 192.168.8.19:5555 shell dumpsys activity services | tr -d '\r' >> dumpsys_activity_19.txt
printf '# --- dumpsys activity providers ---\n' >> dumpsys_activity_19.txt
adb -s 192.168.8.19:5555 shell dumpsys activity providers | tr -d '\r' >> dumpsys_activity_19.txt
```

### dumpsys_activity_drift.txt（690 行，由 .19 样本构造）

漂流/异常样本，用于 C2 鲁棒性断言（预期三字段全空）。构造方式（文件头部 19 行注释亦有记录）：

1. **删除全部锚定行**：原 588 行 `mResumedActivity`、原 609 行 `mFocusedActivity` 删除。
2. **删除主输出 18 条 ServiceRecord 行**（原行号 482, 487, 492, 497, 502, 505, 510,
   513, 516, 521, 524, 527, 532, 537, 542, 545, 548, 553）。其下的孤儿续行
   （`app=`/`created=`/`Connections:`/`act=` 等）**有意保留**，模拟半截 stanza 形态。
3. **删除追加的 services/providers 两段**（原 689-1264 行，含两条 `# ---` 分隔注释行），
   样本中不再有任何 `ServiceRecord` 行。
4. **插入 3 条未知前缀行**（最终行号 20 / 355 / 690）：
   `# injected: unknown format line /dev/null`（另两条带 `copy#2`、`tail` 后缀）。
5. 文件头部 19 行 `#` 注释说明构造方式（为不干扰 C2 锚定，注释刻意不含任何锚定关键字）。

后置条件（已验证）：全文件 grep 无 `mResumedActivity` / `topResumedActivity` /
`mFocusedActivity` / `ServiceRecord`，且无 `# ---` 分隔行。C2 测试应断言该样本
三字段（resumed/top/focused）全空。

## 消费方

- C2（应用检查解析 + 漂移鲁棒性单测）、C3 —— 方案 37 子项 C 的后继任务。
- 文件为真实设备原始输出（除 drift 样本按上述方式注入），未 synthetically 捏造
  真实设备没有的内容。

---

# dumpsys cpuinfo/battery 真机样本 fixtures（方案 37 D1）

本目录追加 Task 19（D1）采集的双真机 `dumpsys cpuinfo` / `dumpsys battery` 原始输出，
供 D2（性能监控解析）单元测试作 fixture 使用。C1 节原文一字未动，本节为纯追加。

## 采集概况

- 采集日期：2026-10-12
- 采集环境：Windows 开发机，adb 36.0.0
- 双机口径：**192.168.8.18:5555（rk3568_r，Android 11）+ 192.168.8.25:5555（rk3288，
  Android 7.1.2）**。第三台 .33 当次离线，按控制器裁决不强求、未采替班样本。
- 归一处理：采集时经 `tr -d '\r'` 去 CR，落盘 UTF-8、LF 换行；6 个文件逐个经
  `open(f, encoding='utf-8').read()` 验证可解码，全部通过；均以换行符结尾。
- 空载如实记录：两机均为空载状态，cpuinfo 行数少（.25 仅 8 条进程行 + 头尾）、
  大量 `0%` 进程、.18 采样窗口内最高占用仅 3.7%。原样入库，未人为加负载或改数值。

## 各文件明细

### dumpsys_cpuinfo_18.txt（66 行）

- 设备：192.168.8.18:5555，Android 11
- 结构：`Load:` 行 + `CPU usage from …:` 头行 + 51 条 `  N% pid/name:` 进程行 +
  11 条 ` +N% pid/name:` 新进程行 + 末行 `3.5% TOTAL: … iowait + … irq + … softirq`
- 版本形态：TOTAL 行含 user/kernel/iowait/irq/softirq 五分量

### dumpsys_cpuinfo_25.txt（12 行）

- 设备：192.168.8.25:5555，Android 7.1.2
- 结构：`Load:` 行 + `CPU usage from …:` 头行 + 8 条进程行 + 末行 `1.9% TOTAL: …`
- 版本形态差异：TOTAL 行仅 user/kernel 两分量（无 iowait/irq/softirq）；进程行无
  `+` 前缀新进程形态。D2 解析须兼容两种 TOTAL 分量集。

采集命令（两机相同模式）：

```bash
adb -s 192.168.8.18:5555 shell dumpsys cpuinfo | tr -d '\r' > dumpsys_cpuinfo_18.txt
adb -s 192.168.8.25:5555 shell dumpsys cpuinfo | tr -d '\r' > dumpsys_cpuinfo_25.txt
```

### dumpsys_battery_18.txt（16 行）

- 设备：192.168.8.18:5555，Android 11
- 字段：AC/USB powered 均 true、status 3、health 2、level 50、voltage 3、
  current 0、temperature 26、technology Li-ion，含 Max charging current/voltage、
  Charge counter 共 15 个字段行

### dumpsys_battery_25.txt（15 行）

- 设备：192.168.8.25:5555，Android 7.1.2
- 双机字段差异（如实入库）：**无 `current:` 行**（.18 有）；`technology:` 键在但值为空；
  USB powered false；status 2（.18 为 3）；temperature 424（.18 为 26）。
  Max charging current/voltage、Charge counter 两机均有。
- D2 注意：`current` 缺失与 `technology` 空值均为真实形态，解析须容忍字段缺省。

采集命令：

```bash
adb -s 192.168.8.18:5555 shell dumpsys battery | tr -d '\r' > dumpsys_battery_18.txt
adb -s 192.168.8.25:5555 shell dumpsys battery | tr -d '\r' > dumpsys_battery_25.txt
```

### dumpsys_cpuinfo_drift.txt（9 行，由 .18 样本裁剪）

漂移样本，供 D2 断言「解析 0 条进程记录且不抛」。构造方式（文件头注释亦有记录，
注释刻意不含任何解析锚点关键字）：

1. **删除汇总锚点行**：末行 `TOTAL` 统计行删除。
2. **删除全部进程占用率行**：51 条 `  N% pid/…` 与 11 条 ` +N% pid/…` 形态全删。
3. **保留设备原有非进程行**：`Load: 0.11 / 0.09 / 0.09` 与 `CPU usage from …:` 两行。
4. **注入 3 条未知前缀行**：`# injected: unknown prefix line copy#1 / copy#2 / tail`。

后置条件（已验证）：文件内 grep 无 `TOTAL`、无任何 `N% pid/` 进程行形态。

### dumpsys_battery_drift.txt（19 行，由 .18 样本裁剪）

漂移样本，供 D2 断言「temperature/voltage/health 三字段解析为空且不抛」。构造方式：

1. **删除三个字段行**：`health: 2`、`voltage: 3`、`temperature: 26` 三行（行首锚定）删除。
2. **其余原行全部保留**：AC/USB/Wireless、Max charging current/voltage、Charge counter、
   status、present、level、scale、current、technology 原样保留。
3. **注入 2 条未知前缀行**：首行后 1 条、文件尾 1 条 `# injected: unknown prefix line …`。
4. 特别说明：保留行中的 `Max charging voltage: 0` 仍含 `voltage` 一词，
   D2 漂移断言**必须以行首字段名锚定**（如 `^\s+voltage:`），勿做裸子串断言。

## 消费方

- D2（性能监控 cpuinfo/battery 解析 + 漂移鲁棒性单测）—— 方案 37 子项 D 的后继任务。
- 双机文件为真实设备原始输出（除 drift 按上述方式裁剪注入），未捏造设备没有的内容。
