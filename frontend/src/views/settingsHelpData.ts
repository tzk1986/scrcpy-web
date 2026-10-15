/**
 * 参数说明手册数据（方案 47 T6）
 * ================================
 *
 * 单一来源：手册文案只存本文件（docs/ 不留副本，避免漂移）；
 * docs/经验记录.md 涉参数调整直接引 /settings/help 页锚点。
 *
 * 结构：每参数一条 ParamHelp——meaning（是什么）+ larger/smaller
 * 双向「影响 + 适用场景」+ range + when_effective（生效时机）。
 * 布尔/枚举参数借 larger/smaller 承载两选项语义，用 largerLabel/
 * smallerLabel 覆写方向词（开/关、h265/h264）。
 *
 * 锚点 id = `<group>-<key>`（anchorId）：bit_rate 在串流/录屏两组
 * 各一条，裸键名会撞 id，故加组前缀保证全局唯一；设置页「?」链接
 * 与本页锚点同用此格式。
 */

export type HelpGroupId = 'stream' | 'recording' | 'override' | 'general'

export interface ParamDirection {
  effect: string
  scenario: string
}

export interface ParamHelp {
  key: string
  group: HelpGroupId
  name: string
  meaning: string
  larger?: ParamDirection
  smaller?: ParamDirection
  largerLabel?: string
  smallerLabel?: string
  range?: string
  when_effective?: string
}

export const HELP_GROUPS: { id: HelpGroupId; title: string }[] = [
  { id: 'stream', title: '串流参数' },
  { id: 'recording', title: '录屏参数' },
  { id: 'override', title: '设备级参数覆盖' },
  { id: 'general', title: '通用说明' },
]

/** 锚点 id：组前缀 + 参数键（见文件头说明） */
export function anchorId(p: Pick<ParamHelp, 'group' | 'key'>): string {
  return `${p.group}-${p.key}`
}

export const PARAM_HELP: ParamHelp[] = [
  {
    key: 'max_size',
    group: 'stream',
    name: '最大分辨率 max_size',
    meaning:
      '视频流长边上限（像素），设备端等比缩放后再编码；0 表示不缩放。' +
      '截图链路恒不缩放，不受此参数影响。',
    larger: {
      effect: '更清晰；设备编码与浏览器解码压力上升、带宽流量上升',
      scenario: '性能强的设备 + 良好网络，追求细节（文字、UI 稿核对）',
    },
    smaller: {
      effect: '省带宽、解码更轻；远看细节发糊',
      scenario: '弱设备降规格；远程/弱网出现卡顿、花屏时降到 720 或 540',
    },
    range: '0 ~ 65535（0 = 不缩放）',
    when_effective: '下次连接生效',
  },
  {
    key: 'bit_rate',
    group: 'stream',
    name: '起始码率 bit_rate',
    meaning:
      '视频流编码目标码率。自适应码率开启时作为起始档，网络变差时自动下探。',
    larger: {
      effect: '画质细节上升、流量上升；弱网下拥塞卡顿风险上升',
      scenario: '带宽充裕、追求画质（演示、投屏画面）',
    },
    smaller: {
      effect: '省流量；画面易糊，快速运动场景更明显',
      scenario: '弱网/流量受限；常用 4M，弱网可降 2M',
    },
    range: '1M ~ 8M（界面常用档）',
    when_effective: '下次连接生效',
  },
  {
    key: 'fps',
    group: 'stream',
    name: '帧率 fps',
    meaning: '视频流目标帧率，设备端按此限帧编码。',
    larger: {
      effect: '动画与操作更顺滑；设备编码、浏览器解码压力与码率需求上升',
      scenario: '游戏、动画、手势调试等要求流畅（60）',
    },
    smaller: {
      effect: '省资源省流量；流畅度下降',
      scenario: '一般调试 30 够用；弱设备/远程可进一步降',
    },
    range: '1 ~ 120（常用 30 / 60）',
    when_effective: '下次连接生效',
  },
  {
    key: 'codec',
    group: 'stream',
    name: '编解码 codec',
    meaning:
      '视频流编码格式。h265 (HEVC) 同画质下码率约为 h264 的一半，' +
      '但需设备（Android 10+）与浏览器解码双支持；不支持时回落 h264 或禁用选项。',
    larger: {
      effect: '省约一半带宽与流量；个别老设备编码慢导致掉帧',
      scenario: '新设备 + 浏览器支持 h265，弱网省流量首选',
    },
    smaller: {
      effect: '兼容性最好、链路最稳；同画质所需码率更高',
      scenario: '旧设备、浏览器不支持 h265，或排查花屏先回退',
    },
    largerLabel: '选 h265',
    smallerLabel: '选 h264',
    when_effective: '下次连接生效',
  },
  {
    key: 'adaptive_bitrate',
    group: 'stream',
    name: '自适应码率 adaptive_bitrate',
    meaning: '开 = 网络变差时自动降码率保流畅；关 = 恒定码率、画质稳定可预期。',
    larger: {
      effect: '弱网自动降码率，优先保流畅（画质随之波动）',
      scenario: '网络质量不稳（WiFi 拥挤、远程连接）',
    },
    smaller: {
      effect: '恒定码率、画质可预期；网络差时更易卡顿',
      scenario: '网络稳定且画质优先（演示、给他人投屏）',
    },
    largerLabel: '开',
    smallerLabel: '关',
    when_effective: '下次连接生效',
  },
  {
    key: 'bit_rate',
    group: 'recording',
    name: '录屏码率 bit_rate',
    meaning: '录屏产物（.mp4）的编码码率，与串流码率相互独立，不影响实时画面。',
    larger: {
      effect: '录像文件更大更清晰',
      scenario: '留存演示/取证素材，磁盘充裕',
    },
    smaller: {
      effect: '文件小省磁盘；画质下降',
      scenario: '批量留档、仅作过程记录',
    },
    range: '1M ~ 8M（界面常用档）',
    when_effective: '每次开始录屏时读取，保存即对新录屏生效',
  },
  {
    key: 'time_limit',
    group: 'recording',
    name: '时长上限 time_limit',
    meaning:
      '单次录屏的分段时长上限（秒）。0 表示不限时——靠手动停止 + ' +
      '磁盘双限清理（方案 42）兜底。',
    larger: {
      effect: '单文件更长更大，减少手动续录次数',
      scenario: '完整长过程一次录完，磁盘充裕',
    },
    smaller: {
      effect: '单文件小、便于分段管理；长过程需手动续录',
      scenario: '分段留档、控制单文件体积',
    },
    range: '1 ~ 1800 秒（0 = 不限时）',
    when_effective: '每次开始录屏时读取',
  },
  {
    key: 'override',
    group: 'override',
    name: '设备级覆盖语义',
    meaning:
      '为单台设备保存一份串流参数覆盖：弱设备降规格、全局设置不动。' +
      '保存时整节提交（表单里所有串流键一并写入，不只是改过的项）；' +
      '清除后该设备回到全局值。各串流参数的双向影响见「串流参数」组。',
    when_effective: '保存后该设备下次连接生效；清除回到全局',
  },
  {
    key: 'source',
    group: 'general',
    name: '来源徽标',
    meaning:
      '「生效参数」面板标注每个键当前值的来源：出厂默认（内置值）/ ' +
      '用户设置（设置页保存）/ 环境覆盖（环境变量或 config.yaml，' +
      '优先级高于用户设置）。',
  },
  {
    key: 'effective',
    group: 'general',
    name: '生效时机',
    meaning:
      '串流段（含设备级覆盖）下次连接生效——已打开的页面不受影响，' +
      '重连后按新值起流；录屏段每次开始录屏时读取，保存即对下一个新录屏生效。',
  },
]
