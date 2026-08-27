import type { CitationPage, ChatMessage, StreamHandlers } from './types'

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response
  let message = `请求失败（${response.status}）`
  try {
    const body = await response.json()
    message = body?.error?.message || message
  } catch { /* normalized fallback */ }
  const retry = response.headers.get('Retry-After')
  throw new Error(retry ? `${message}，约 ${retry} 秒后可重试` : message)
}

export async function createConversation(): Promise<string> {
  const response = await checked(await fetch('/qa/v1/conversations', { method: 'POST', credentials: 'same-origin' }))
  const body = await response.json()
  return body.id
}

export async function loadHistory(id: string): Promise<ChatMessage[]> {
  const response = await checked(await fetch(`/qa/v1/conversations/${encodeURIComponent(id)}/messages`, { credentials: 'same-origin' }))
  return (await response.json()).messages || []
}

export async function deleteConversation(id: string): Promise<void> {
  await checked(await fetch(`/qa/v1/conversations/${encodeURIComponent(id)}`, { method: 'DELETE', credentials: 'same-origin' }))
}

export async function stopTurn(conversation: string, turn: string): Promise<void> {
  await checked(await fetch(`/qa/v1/conversations/${encodeURIComponent(conversation)}/turns/${encodeURIComponent(turn)}/stop`, { method: 'POST', credentials: 'same-origin' }))
}

export async function loadCitation(slug: string): Promise<CitationPage> {
  const response = await checked(await fetch(`/qa/v1/citations/${slug.split('/').map(encodeURIComponent).join('/')}`, { credentials: 'same-origin' }))
  return response.json()
}

export async function streamQuestion(conversation: string, query: string, handlers: StreamHandlers, signal: AbortSignal): Promise<void> {
  const response = await checked(await fetch(`/qa/v1/conversations/${encodeURIComponent(conversation)}/messages:stream`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ query }),
    signal,
  }))
  if (!response.body) throw new Error('浏览器不支持流式回答')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() || ''
    for (const frame of frames) dispatchFrame(frame, handlers)
    if (done) break
  }
  if (buffer.trim()) dispatchFrame(buffer, handlers)
}

export function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = ''
  const data: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trim())
  }
  if (!event || !(event in handlers) || !data.length) return
  try { (handlers[event as keyof StreamHandlers] as (value: unknown) => void)(JSON.parse(data.join('\n'))) } catch { /* malformed public frame is ignored */ }
}
