import { describe, expect, it, vi } from 'vitest'

import { isH265DecoderSupported } from './codecSupport'

describe('isH265DecoderSupported', () => {
  it('returns false when VideoDecoder API is missing', async () => {
    vi.stubGlobal('VideoDecoder', undefined)
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })

  it('returns true when the codec is supported', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockResolvedValue({ supported: true }),
    })
    expect(await isH265DecoderSupported()).toBe(true)
    vi.unstubAllGlobals()
  })

  it('returns false when the codec is unsupported', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockResolvedValue({ supported: false }),
    })
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })

  it('returns false when probing throws', async () => {
    vi.stubGlobal('VideoDecoder', {
      isConfigSupported: vi.fn().mockRejectedValue(new Error('probe failed')),
    })
    expect(await isH265DecoderSupported()).toBe(false)
    vi.unstubAllGlobals()
  })
})
