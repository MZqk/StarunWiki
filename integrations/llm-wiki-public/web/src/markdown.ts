import DOMPurify from 'dompurify'
import { Marked, Renderer } from 'marked'

const wikiLink = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g
const renderer = new Renderer()
renderer.html = ({ text }) => escapeHTML(text)
const markdown = new Marked({ renderer })

function escapeHTML(value: string): string {
  return value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character] || character)
}

export function wikiLinksToMarkdown(source: string): string {
  return source.replace(wikiLink, (_whole, slug: string, label?: string) => {
    const safeSlug = slug.split('/').map(encodeURIComponent).join('/')
    const safeLabel = (label || slug).replace(/[\[\]]/g, '')
    return `[${safeLabel}](/qa/v1/citations/${safeSlug})`
  })
}

export function renderMarkdown(source: string): string {
  const rendered = markdown.parse(wikiLinksToMarkdown(source), { async: false, breaks: true }) as string
  return DOMPurify.sanitize(rendered, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'svg', 'math'],
    FORBID_ATTR: ['style', 'srcset'],
    ALLOW_UNKNOWN_PROTOCOLS: false,
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|\/qa\/v1\/citations\/|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i,
  })
}
