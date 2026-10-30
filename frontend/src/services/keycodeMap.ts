/**
 * 物理键盘 → Android KEYCODE 映射（方案 54 G1）
 * ==============================================
 *
 * 以 KeyboardEvent.code（物理键位，跨键盘布局稳定）为键。
 * 取值来自 Android KeyEvent 常量表（API 21+，与上游 scrcpy
 * sdk 键盘模式同源）。
 *
 * 注意与直觉不同的两处：
 *   - 浏览器 Home/End 键 → KEYCODE_MOVE_HOME/MOVE_END（文档键），
 *     不是 Android 的 HOME(3)（虚拟键另有入口）。
 *   - 修饰键（Shift/Ctrl/Alt/Meta）按下时 metaState 由各事件的
 *     修饰标志合成，同时修饰键本身也作 keycode 注入（对齐上游行为，
 *     设备侧组合键依赖按下态）。
 */

/** Android KeyEvent.META_* 位掩码 */
export const MetaState = {
  SHIFT_ON: 0x1,
  ALT_ON: 0x2,
  SYM_ON: 0x4,
  CTRL_ON: 0x10,
  META_ON: 0x10000,
} as const

/** 事件修饰标志 → metaState 位掩码 */
export function metaStateFromEvent(e: {
  shiftKey: boolean
  ctrlKey: boolean
  altKey: boolean
  metaKey: boolean
}): number {
  let meta = 0
  if (e.shiftKey) meta |= MetaState.SHIFT_ON
  if (e.altKey) meta |= MetaState.ALT_ON
  if (e.ctrlKey) meta |= MetaState.CTRL_ON
  if (e.metaKey) meta |= MetaState.META_ON
  return meta
}

/** KeyboardEvent.code → Android keycode（方案 54 全表） */
export const KEYCODE_BY_CODE: Readonly<Record<string, number>> = {
  // 字母 A-Z（29-54）
  KeyA: 29, KeyB: 30, KeyC: 31, KeyD: 32, KeyE: 33, KeyF: 34, KeyG: 35,
  KeyH: 36, KeyI: 37, KeyJ: 38, KeyK: 39, KeyL: 40, KeyM: 41, KeyN: 42,
  KeyO: 43, KeyP: 44, KeyQ: 45, KeyR: 46, KeyS: 47, KeyT: 48, KeyU: 49,
  KeyV: 50, KeyW: 51, KeyX: 52, KeyY: 53, KeyZ: 54,
  // 主键盘数字（0→7 … 9→16）
  Digit0: 7, Digit1: 8, Digit2: 9, Digit3: 10, Digit4: 11,
  Digit5: 12, Digit6: 13, Digit7: 14, Digit8: 15, Digit9: 16,
  // 符号
  Space: 62,
  Minus: 69, Equal: 70, BracketLeft: 71, BracketRight: 72,
  Backslash: 73, Semicolon: 74, Quote: 75, Comma: 55, Period: 56,
  Slash: 76, Backquote: 68,
  // 编辑/导航
  Enter: 66, NumpadEnter: 160, Tab: 61, Backspace: 67, Escape: 111,
  ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22,
  PageUp: 92, PageDown: 93,
  Home: 122,     // MOVE_HOME，非 Android HOME(3)
  End: 123,      // MOVE_END
  Insert: 124, Delete: 112,
  CapsLock: 115,
  // 功能键 F1-F12（131-142）
  F1: 131, F2: 132, F3: 133, F4: 134, F5: 135, F6: 136,
  F7: 137, F8: 138, F9: 139, F10: 140, F11: 141, F12: 142,
  // 修饰键
  ShiftLeft: 59, ShiftRight: 60,
  ControlLeft: 113, ControlRight: 114,
  AltLeft: 57, AltRight: 58,
  MetaLeft: 117, MetaRight: 118,
  // 小键盘（NumLock 态按数字意义注入）
  Numpad0: 144, Numpad1: 145, Numpad2: 146, Numpad3: 147, Numpad4: 148,
  Numpad5: 149, Numpad6: 150, Numpad7: 151, Numpad8: 152, Numpad9: 153,
  NumpadAdd: 154, NumpadSubtract: 155, NumpadMultiply: 156,
  NumpadDivide: 157, NumpadDecimal: 158, NumpadComma: 159,
  // 音量
  AudioVolumeUp: 24, AudioVolumeDown: 25, AudioVolumeMute: 164,
}

/** code 是否字母键（D2 prefer-text 判定用） */
export function isLetterCode(code: string): boolean {
  return /^Key[A-Z]$/.test(code)
}

export function keycodeForCode(code: string): number | null {
  return KEYCODE_BY_CODE[code] ?? null
}
