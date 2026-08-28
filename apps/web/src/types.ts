export type Citation = {
  slug: string
  title: string
  summary: string
  source_status: string
  review_state: string
  release_id: string
  is_draft: boolean
  needs_review: boolean
}

export type MetaSuggestion = {
  category: string
  title: string
  question: string
}

export type PublicMeta = {
  brand: string
  pack_id: string
  title: string
  description: string
  locale: string
  release_id: string
  release_mode: string
  page_count: number
  draft_count: number
  unreviewed_count: number
  corpus_verified: boolean
  suggestions: MetaSuggestion[]
}

export type ChatMessage = {
  role: 'user' | 'assistant'
  content: string
  created_at?: string
  citations?: Citation[]
  contains_draft?: boolean
  contains_unreviewed?: boolean
  streaming?: boolean
  stopped?: boolean
  error?: string
}

export type CitationPage = Citation & {
  content: string
  source_access: string
  payload_sha256: string
}

export type StreamHandlers = {
  turn(data: { turn_id: string }): void
  delta(data: { text: string }): void
  citations(data: { items: Citation[]; contains_draft: boolean; contains_unreviewed: boolean }): void
  done(data: { stopped: boolean; contains_draft?: boolean; contains_unreviewed?: boolean }): void
  error(data: { code: string; message: string }): void
}
