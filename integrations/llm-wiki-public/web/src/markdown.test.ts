import assert from 'node:assert/strict'
import test from 'node:test'
import { JSDOM } from 'jsdom'
import { dispatchFrame } from './api'

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, Node: dom.window.Node })
const { renderMarkdown, wikiLinksToMarkdown } = await import('./markdown')

test('wiki links become only local citation links', () => {
  assert.equal(wikiLinksToMarkdown('见 [[concept/ai/agent|Agent]]'), '见 [Agent](/qa/v1/citations/concept/ai/agent)')
})

test('stream dispatcher exposes only declared public events', () => {
  const seen: string[] = []
  const handlers = {
    turn: () => seen.push('turn'), delta: () => seen.push('delta'), citations: () => seen.push('citations'), done: () => seen.push('done'), error: () => seen.push('error'),
  }
  dispatchFrame('event: thinking\ndata: {"secret":true}', handlers)
  dispatchFrame('event: delta\ndata: {"text":"ok"}', handlers)
  assert.deepEqual(seen, ['delta'])
})

test('final markdown removes raw script handlers and dangerous URLs', () => {
  const html = renderMarkdown('<script>alert(1)</script><img src="x" onerror="alert(2)">[bad](javascript:alert(3))')
	const document = new JSDOM(`<body>${html}</body>`).window.document
	assert.equal(document.querySelector('script, img'), null)
	assert.equal([...document.querySelectorAll('a')].some(link => link.href.startsWith('javascript:')), false)
})
