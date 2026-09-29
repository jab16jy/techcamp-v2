import {
  PHOTO_CONTENT_TYPE,
  compressPhotoWithinLimit,
  encodedPhoto,
  type EncodedPhoto,
  type EncodeStep,
  type PhotoContentType,
} from './compress'

/**
 * The one place that owns a canvas (ADR-0018: the client compresses to
 * ≤ 200 KB, strips EXIF, and PUTs the bytes with a presigned URL).
 *
 * Stripping EXIF is a consequence of this pipeline rather than an explicit
 * step: decoding to a bitmap and drawing it into a canvas keeps pixels only,
 * and re-encoding writes a fresh JPEG with no metadata — so the GPS tag of a
 * phone photo never reaches object storage.
 *
 * jsdom has neither `createImageBitmap` nor a canvas that encodes, so this
 * module has no unit test of its own: what it guarantees is that it drives the
 * pure ladder of `compress.ts` and hands the ladder's result straight through.
 */

export class PhotoDecodeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PhotoDecodeError'
  }
}

export const PHOTO_NOT_AN_IMAGE_MESSAGE =
  'Ese archivo no es una imagen. Elige una foto de la cámara o la galería.'

/** A surface we can draw on and read bytes back from. */
type DrawingSurface = OffscreenCanvas | HTMLCanvasElement

function createSurface(width: number, height: number): DrawingSurface {
  // OffscreenCanvas where it exists (Chrome, Firefox, Safari 16.4+); a plain
  // canvas element everywhere else. Both are followed by the same
  // `toBlob`-shaped call below.
  if (typeof OffscreenCanvas === 'function') return new OffscreenCanvas(width, height)
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  return canvas
}

function surfaceToBlob(
  surface: DrawingSurface,
  contentType: PhotoContentType,
  quality: number,
): Promise<Blob> {
  if ('convertToBlob' in surface) {
    return surface.convertToBlob({ type: contentType, quality })
  }
  return new Promise((resolve, reject) => {
    surface.toBlob(
      (blob) => {
        if (blob === null) {
          reject(new PhotoDecodeError('the browser could not encode the photo'))
          return
        }
        resolve(blob)
      },
      contentType,
      quality,
    )
  })
}

async function encodeStep(bitmap: ImageBitmap, step: EncodeStep): Promise<EncodedPhoto> {
  const longest = Math.max(bitmap.width, bitmap.height)
  const scale = Math.min(1, step.maxEdge / longest)
  const width = Math.max(1, Math.round(bitmap.width * scale))
  const height = Math.max(1, Math.round(bitmap.height * scale))
  const surface = createSurface(width, height)
  const context = surface.getContext('2d')
  if (context === null) throw new PhotoDecodeError('the browser gave no 2d canvas context')
  // JPEG has no alpha channel, so a transparent source (a screenshot, a PNG
  // export) would come out with a black background instead of the app's cream.
  context.fillStyle = '#ffffff'
  context.fillRect(0, 0, width, height)
  context.drawImage(bitmap, 0, 0, width, height)
  const blob = await surfaceToBlob(surface, PHOTO_CONTENT_TYPE, step.quality)
  return encodedPhoto(new Uint8Array(await blob.arrayBuffer()), PHOTO_CONTENT_TYPE)
}

/**
 * Decodes a chosen file, re-encodes it down the ladder until it fits under
 * 200 KB, and refuses it when nothing fits (the user sees the message; nothing
 * is stored).
 *
 * `imageOrientation: 'from-image'` is the default, and it is passed on
 * purpose: a phone photo carries its rotation in EXIF, and a decoder that
 * ignored it would upload every landscape shot sideways.
 */
export async function compressPhoto(file: Blob): Promise<EncodedPhoto> {
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    throw new PhotoDecodeError(PHOTO_NOT_AN_IMAGE_MESSAGE)
  }
  try {
    return await compressPhotoWithinLimit((step) => encodeStep(bitmap, step))
  } finally {
    bitmap.close()
  }
}
