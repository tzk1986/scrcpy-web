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

## 各文件明细

### dumpsys_activity_18.txt（11924 行）

- 设备：192.168.8.18:5555，型号 rk3568_r，Android 11
- 结构：主输出 1-10784；services 段 10785-11521（68 条 ServiceRecord）；
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
- 结构：主输出 1-803；services 段 804-1398（28 条 ServiceRecord）；
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