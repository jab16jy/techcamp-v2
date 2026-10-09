import { InfoIcon } from '../../../design-system/ui/icons'
import { ASSISTANT_COPY } from '../api/assistantCopy'
import type { AssistantNotice } from '../api/useAskAssistant'

export interface NoticeBannerProps {
  notice: AssistantNotice
}

/**
 * The one sentence a question that was not answered deserves (docs/07 §Asistente).
 *
 * A title and a line of plain Spanish, never an outcome code: the producer has to
 * tell "the day is over" from "the phone is offline" from "it broke" at a glance,
 * and only words do that. The tone icon carries the same distinction, because a
 * screen read in direct sun cannot rely on colour alone (docs/07 §Principios).
 */
export function NoticeBanner({ notice }: NoticeBannerProps) {
  const copy = NOTICE_COPY[notice]
  return (
    <div
      // `status` rather than `alert`: this is a settled outcome of a question the
      // producer already asked, and an assertive announcement would interrupt the
      // screen reader mid-read of the conversation above it.
      role="status"
      className="flex items-start gap-2 border-b border-text/15 bg-surface-raised px-4 py-3"
    >
      <InfoIcon className="mt-0.5 size-5 shrink-0 text-text-muted" />
      <div>
        <p className="text-base font-semibold">{copy.title}</p>
        <p className="text-sm text-text-muted">{copy.body}</p>
      </div>
    </div>
  )
}

/** The three notices docs/07 §Asistente draws, in the copy it mandates. */
const NOTICE_COPY: Record<AssistantNotice, { title: string; body: string }> = {
  limited: { title: ASSISTANT_COPY.limitedTitle, body: ASSISTANT_COPY.limitedBody },
  offline: { title: ASSISTANT_COPY.offlineTitle, body: ASSISTANT_COPY.offlineBody },
  failed: { title: ASSISTANT_COPY.failedTitle, body: ASSISTANT_COPY.failedBody },
}