import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { loadMeta, parsePublicMeta } from './api'

const publicMeta = {
  brand: 'Evidence Stack',
  pack_id: 'deep-sky',
  title: '深空知识问答',
  description: '只读深空摄影知识快照',
  locale: 'zh-CN',
  release_id: 'public-de219d707e39',
  release_mode: 'fixed-corpus-snapshot',
  page_count: 51,
  draft_count: 0,
  unreviewed_count: 51,
  corpus_verified: true,
  suggestions: [
    { category: '现场拍摄', title: '城市阳台首拍怎么开始？', question: '城市阳台第一次拍深空，应该先做什么？' },
  ],
}

test('public meta parser accepts the flat pack contract', () => {
  assert.deepEqual(parsePublicMeta(publicMeta), publicMeta)
})

test('public meta parser rejects inconsistent counts and incomplete suggestions', () => {
  assert.throws(
    () => parsePublicMeta({ ...publicMeta, unreviewed_count: 52 }),
    /页面计数不一致/,
  )
  assert.throws(
    () => parsePublicMeta({ ...publicMeta, suggestions: [{ category: '现场拍摄', title: '首拍' }] }),
    /question/,
  )
  assert.throws(
    () => parsePublicMeta({ ...publicMeta, locale: 'not_a_locale' }),
    /locale/,
  )
})

test('loadMeta requests the public same-origin endpoint', async () => {
  const originalFetch = globalThis.fetch
  let requestedURL = ''
  let requestedInit: RequestInit | undefined
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requestedURL = String(input)
    requestedInit = init
    return new Response(JSON.stringify(publicMeta), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  try {
    assert.deepEqual(await loadMeta(), publicMeta)
    assert.equal(requestedURL, '/qa/v1/meta')
    assert.equal(requestedInit?.method, 'GET')
    assert.equal(requestedInit?.credentials, 'same-origin')
    assert.equal(new Headers(requestedInit?.headers).get('Accept'), 'application/json')
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('web shell binds release metadata without a stale page-count literal', () => {
  const app = readFileSync(new URL('./App.vue', import.meta.url), 'utf8')
  const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
  assert.equal(app.includes('50 PAGE'), false)
  assert.equal(app.includes('当前 50 个 Wiki'), false)
  assert.equal(html.includes('50 个'), false)
  for (const binding of [
    'meta?.description',
    'meta.value.release_id',
    'meta.value.release_mode',
    'meta.value.page_count',
    'meta.value.draft_count',
    'meta.value.unreviewed_count',
    'meta.corpus_verified',
    'meta?.suggestions || []',
    'suggestion.question',
  ]) assert.match(app, new RegExp(binding.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')))
})
