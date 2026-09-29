import { uuidv7 } from '../db/ids'
import { savePhoto } from '../db/photos'
import type { PhotoRow, SyncEntity } from '../db/db'
import { compressPhoto } from './browser'
import type { EncodedPhoto } from './compress'

/**
 * Taking a photo in the field: compress it, then store it against its parent
 * (ADR-0018: the bytes stay on the phone until a presigned URL lets them go
 * straight to object storage; docs/06 §7 "fotos pendientes").
 *
 * The compressor is a parameter so the rule "a photo that will not fit is
 * refused and nothing is stored" is provable without a canvas, which jsdom
 * has none of.
 */
export async function addPhoto(
  input: { file: Blob; entity: SyncEntity; parentId: string },
  compress: (file: Blob) => Promise<EncodedPhoto> = compressPhoto,
): Promise<PhotoRow> {
  // Compression happens before any write: a photo that will not fit under the
  // ceiling must leave nothing behind, not a rejected row to clean up.
  const encoded = await compress(input.file)
  return savePhoto({
    id: uuidv7(),
    entity: input.entity,
    parent_id: input.parentId,
    data: encoded.data,
    content_type: encoded.content_type,
    bytes: encoded.bytes,
  })
}
