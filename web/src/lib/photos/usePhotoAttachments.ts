import { useEffect, useMemo, useRef, useState } from 'react'
import { uuidv7 } from '../db/ids'
import { usePhotos } from '../db/live'
import { photoBlob, removePhoto } from '../db/photos'
import type { PhotoRow, SyncEntity } from '../db/db'
import { attachCompressedPhoto } from './addPhoto'
import { PhotoTooLargeError, encodedBlob, type EncodedPhoto } from './compress'
import { PHOTO_NOT_AN_IMAGE_MESSAGE, PhotoDecodeError, compressPhoto } from './browser'

/**
 * The photo block of the two sheets, shared because docs/07 forbids one
 * feature importing another: `logbook` and `visits` both render the same
 * control over the same store.
 *
 * A photo is chosen and compressed right away (a 4 MB camera file is refused
 * or shrunk while the sheet is open, not minutes later on save), and staged
 * until the record it belongs to has an id — the entry or visit is only written
 * when the user saves, and a photo cannot be stored against an id that does not
 * exist yet. So the same block shows two kinds of photo: staged ones (about to
 * be attached) and the ones already on the phone (their state comes from the
 * upload queue).
 */

export interface PhotoAttachmentItem {
  id: string
  /** Object URL of the compressed photo; null once it left the phone. */
  previewUrl: string | null
  status: 'pending' | 'uploaded' | 'failed'
  error: string | null
}

interface StagedPhoto {
  id: string
  encoded: EncodedPhoto
  previewUrl: string
}

export interface PhotoAttachments {
  items: PhotoAttachmentItem[]
  /** Why the last chosen photo was refused (Spanish, shown under the control). */
  error: string | null
  addFiles: (files: FileList | null) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Stores every staged photo against the record that was just saved. */
  attachTo: (parentId: string) => Promise<void>
  reset: () => void
}

export function usePhotoAttachments(
  entity: SyncEntity,
  parentId: string | null,
  compress: (file: Blob) => Promise<EncodedPhoto> = compressPhoto,
): PhotoAttachments {
  const stored = usePhotos(entity, parentId)
  const storedPreviews = useStoredPreviews(stored)
  const [staged, setStaged] = useState<StagedPhoto[]>([])
  const [error, setError] = useState<string | null>(null)
  // The unmount cleanup needs the staged list without re-subscribing on every
  // chosen photo, so it reads a ref the render pass keeps fresh.
  const stagedRef = useRef<StagedPhoto[]>(staged)
  useEffect(() => {
    stagedRef.current = staged
  }, [staged])

  useEffect(
    () => () => {
      for (const photo of stagedRef.current) URL.revokeObjectURL(photo.previewUrl)
    },
    [],
  )

  async function addFiles(files: FileList | null): Promise<void> {
    if (files === null) return
    setError(null)
    for (const file of Array.from(files)) {
      let encoded: EncodedPhoto
      try {
        encoded = await compress(file)
      } catch (reason) {
        // A refused photo leaves the ones already staged alone, and the sheet
        // says why instead of silently keeping a file it cannot send.
        setError(refusalMessage(reason))
        return
      }
      const photo: StagedPhoto = {
        id: uuidv7(),
        encoded,
        previewUrl: URL.createObjectURL(encodedBlob(encoded)),
      }
      setStaged((current) => [...current, photo])
    }
  }

  async function remove(id: string): Promise<void> {
    const photo = staged.find((candidate) => candidate.id === id)
    if (photo !== undefined) {
      URL.revokeObjectURL(photo.previewUrl)
      setStaged((current) => current.filter((candidate) => candidate.id !== id))
      return
    }
    await removePhoto(id)
  }

  async function attachTo(savedParentId: string): Promise<void> {
    for (const photo of staged) {
      await attachCompressedPhoto(entity, savedParentId, photo.encoded)
    }
    discardStaged(staged)
    setStaged([])
  }

  return {
    items: [
      ...stored.map(
        (photo): PhotoAttachmentItem => ({
          id: photo.id,
          previewUrl: storedPreviews.get(photo.id) ?? null,
          status: photo.status,
          error: photo.error,
        }),
      ),
      ...staged.map(
        (photo): PhotoAttachmentItem => ({
          id: photo.id,
          previewUrl: photo.previewUrl,
          status: 'pending',
          error: null,
        }),
      ),
    ],
    error,
    addFiles,
    remove,
    attachTo,
    reset: () => {
      discardStaged(stagedRef.current)
      setStaged([])
      setError(null)
    },
  }
}

function discardStaged(photos: StagedPhoto[]): void {
  for (const photo of photos) URL.revokeObjectURL(photo.previewUrl)
}

/**
 * One object URL per stored photo, for as long as the store still has its
 * bytes. They are revoked whenever the list changes and on unmount: an object
 * URL pins its blob in memory, and a phone that kept every photo of the season
 * pinned would eventually be the reason a save fails.
 *
 * They are built while rendering, which is the one thing `useMemo` is not meant
 * for, because the alternative — putting them in state from an effect — makes
 * React cascade a render per photo. The cost is bounded and dev-only: React's
 * double render can leave one unrevoked URL behind, and the browser frees
 * those with the document.
 */
function useStoredPreviews(rows: PhotoRow[]): Map<string, string> {
  const previews = useMemo(() => {
    const urls = new Map<string, string>()
    for (const row of rows) {
      const blob = photoBlob(row)
      if (blob !== null) urls.set(row.id, URL.createObjectURL(blob))
    }
    return urls
  }, [rows])

  useEffect(
    () => () => {
      for (const url of previews.values()) URL.revokeObjectURL(url)
    },
    [previews],
  )

  return previews
}

/** The message a refused photo shows, never a raw exception. */
function refusalMessage(reason: unknown): string {
  if (reason instanceof PhotoTooLargeError || reason instanceof PhotoDecodeError) return reason.message
  return PHOTO_NOT_AN_IMAGE_MESSAGE
}
