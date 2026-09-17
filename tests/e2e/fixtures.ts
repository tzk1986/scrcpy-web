import { test as base, expect } from '@playwright/test'

export interface DeviceInfo {
  id: string
  model: string
  status: string
  resolution: [number, number]
}

type Fixtures = {
  device: DeviceInfo | null
  shell: (cmd: string) => Promise<string>
}

// 设备探测：取第一个 online 设备；无设备返回 null，由 spec 决定 skip。
// shell 通道：POST /api/debug/sessions?device_id&user_id → session_id，
// 再 POST /api/debug/sessions/{sid}/shell?command=...，返回 stdout 文本。
export const test = base.extend<Fixtures>({
  device: async ({ request }, use, testInfo) => {
    const res = await request.get('/api/devices')
    const devices = (await res.json()) as DeviceInfo[]
    const dev = devices.find((d) => d.status === 'online') ?? null
    if (!dev) {
      testInfo.annotations.push({ type: 'skip-reason', description: 'no online ADB device' })
    }
    await use(dev)
  },

  shell: async ({ device, request }, use) => {
    if (!device) {
      await use(async () => {
        throw new Error('shell 需要在线设备：请连接 ADB 设备后重试')
      })
      return
    }
    const createRes = await request.post('/api/debug/sessions', {
      params: { device_id: device.id, user_id: 'e2e-runner' },
    })
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string }
    try {
      await use(async (cmd: string) => {
        const res = await request.post(`/api/debug/sessions/${sessionId}/shell`, {
          params: { command: cmd },
        })
        const { output } = (await res.json()) as { output: string }
        return output
      })
    } finally {
      await request.delete(`/api/debug/sessions/${sessionId}`)
    }
  },
})

export { expect }
