/**
 * Client-side photo compression (ADR-0018: the client compresses to ≤ 200 KB
 * and strips EXIF; docs/04 §Bitácora "Fotos" and docs/06 §7 "Almacenamiento"
 * are the client side of that rule).
 *
 * Everything here is pure on purpose: the ladder of encodings and the walk
 * down it know nothing about canvases or the DOM, so the rule that matters —
 * "a photo over the ceiling is refused, never stored" — is provable in jsdom
 * with a fake encoder. `browser.ts` is the thin adapter that owns the canvas.
 */

/** ADR-0018 / docs/04 §Bitácora: the ceiling the API enforces with `422`. */
export const PHOTO_MAX_BYTES = 200 * 1024

/** docs/04 §Bitácora accepts only these two on `content_type`. */
export type PhotoContentType = 'image/jpeg' | 'image/webp'

export const PHOTO_CONTENT_TYPE: PhotoContentType = 'image/jpeg'

/** One rung of the ladder: the longest edge allowed and the JPEG quality. */
export interface EncodeStep {
  maxEdge: number
  quality: number
}

/**
 * Down in order, largest first, because a phone camera photo is usually far
 * under the ceiling and the user should get the best photo the rule allows
 * rather than the smallest one. `quality` alone cannot rescue a very large
 * photo (quality does not scale below a floor), hence the shrinking edge.
 */
export const ENCODE_STEPS: readonly EncodeStep[] = [
  { maxEdge: 1600, quality: 0.82 },
  { maxEdge: 1600, quality: 0.65 },
  { maxEdge: 1200, quality: 0.5 },
  { maxEdge: 900, quality: 0.4 },
  { maxEdge: 640, quality: 0.3 },
]

export interface EncodedPhoto {
  data: Uint8Array
  content_type: PhotoContentType
  /** Always `data.byteLength`, never a number typed twice. */
  bytes: number
}

/**
 * Builds an encoding with its length derived from its bytes.
 *
 * The upload queue PUTs `data` and tells presign `bytes`, and the presigned
 * URL signs that number as `Content-Length` (T6): the two can only agree if
 * one is computed from the other.
 */
export function encodedPhoto(data: Uint8Array, contentType: PhotoContentType): EncodedPhoto {
  return { data, content_type: contentType, bytes: data.byteLength }
}

export type PhotoEncoder = (step: EncodeStep) => Promise<EncodedPhoto>

/**
 * Tries each step in order and returns the first encoding at or under the
 * ceiling, or null when none of them fits — the caller refuses, because a
 * stored oversized photo would be rejected by the API later and would still
 * be taking up the phone's storage.
 */
export async function encodeWithinLimit(encode: PhotoEncoder): Promise<EncodedPhoto | null> {
  for (const step of ENCODE_STEPS) {
    const encoded = await encode(step)
    if (encoded.bytes <= PHOTO_MAX_BYTES) return encoded
  }
  return null
}

/** Plain Spanish, because it is what the sheet shows the user (docs/07 copy). */
export const PHOTO_TOO_LARGE_MESSAGE =
  'La foto no se pudo reducir a 200 KB. Tómala de nuevo más lejos o con menos luz.'

/** A photo that no step of the ladder could bring under the ceiling. */
export class PhotoTooLargeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PhotoTooLargeError'
  }
}

/** `encodeWithinLimit` as the caller uses it: refuse instead of returning null. */
export async function compressPhotoWithinLimit(encode: PhotoEncoder): Promise<EncodedPhoto> {
  const encoded = await encodeWithinLimit(encode)
  if (encoded === null) throw new PhotoTooLargeError(PHOTO_TOO_LARGE_MESSAGE)
  return encoded
}
