import { describe, expect, it } from 'vitest'
import {
  ENCODE_STEPS,
  PHOTO_MAX_BYTES,
  compressPhotoWithinLimit,
  encodeWithinLimit,
  encodedPhoto,
  type EncodeStep,
  type PhotoEncoder,
} from './compress'

/** An encoding of `bytes` bytes, whatever step asked for it. */
function encodingOf(bytes: number): ReturnType<typeof encodedPhoto> {
  return encodedPhoto(new Uint8Array(bytes), 'image/jpeg')
}

describe('the encoding step loop', () => {
  it('returns the first encoding at or under the ceiling and stops there', async () => {
    const asked: EncodeStep[] = []
    const encode: PhotoEncoder = async (step) => {
      asked.push(step)
      // Only the second step fits; anything after it would be a smaller photo
      // for nothing.
      return encodingOf(asked.length === 1 ? PHOTO_MAX_BYTES + 1 : 150 * 1024)
    }

    const encoded = await encodeWithinLimit(encode)

    expect(encoded?.bytes).toBe(150 * 1024)
    expect(asked).toHaveLength(2)
    expect(asked[0]).toEqual(ENCODE_STEPS[0])
  })

  it('walks every step and refuses when none of them fits', async () => {
    let calls = 0
    const encode: PhotoEncoder = async () => {
      calls += 1
      return encodingOf(PHOTO_MAX_BYTES + 1)
    }

    const result = await encodeWithinLimit(encode)

    expect(result).toBeNull()
    expect(calls).toBe(ENCODE_STEPS.length)
  })
})

describe('a photo that cannot be compressed enough', () => {
  it('is refused with the message the user reads, not stored oversized', async () => {
    await expect(
      compressPhotoWithinLimit(async () => encodingOf(PHOTO_MAX_BYTES + 1)),
    ).rejects.toThrow('200 KB')
  })
})

describe('an encoding', () => {
  it('declares its own byte length, so it cannot disagree with its bytes', () => {
    const data = new Uint8Array(7)

    expect(encodedPhoto(data, 'image/jpeg').bytes).toBe(data.byteLength)
  })
})
