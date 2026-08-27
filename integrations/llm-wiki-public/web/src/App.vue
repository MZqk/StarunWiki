<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { createConversation, deleteConversation, loadCitation, loadHistory, stopTurn, streamQuestion } from './api'
import { renderMarkdown } from './markdown'
import type { ChatMessage, Citation, CitationPage } from './types'

type SavedConversation = { id: string; createdAt: number; preview: string }

const sevenDays = 7 * 24 * 60 * 60 * 1000
const conversations = ref<SavedConversation[]>(readSaved())
const activeID = ref('')
const messages = ref<ChatMessage[]>([])
const query = ref('')
const busy = ref(false)
const currentTurn = ref('')
const requestController = ref<AbortController | null>(null)
const lastQuestion = ref('')
const pageError = ref('')
const drawer = ref<CitationPage | null>(null)
const drawerBusy = ref(false)
const messagePane = ref<HTMLElement | null>(null)
const showHistory = ref(false)

const canSend = computed(() => query.value.trim().length > 0 && query.value.length <= 2000 && !busy.value)
const currentSaved = computed(() => conversations.value.find(item => item.id === activeID.value))

onMounted(async () => {
  if (conversations.value.length) await selectConversation(conversations.value[0].id)
})

function readSaved(): SavedConversation[] {
  try {
    const parsed = JSON.parse(localStorage.getItem('llm-wiki-public-conversations') || '[]') as SavedConversation[]
    const current = parsed.filter(item => Date.now() - item.createdAt < sevenDays)
    localStorage.setItem('llm-wiki-public-conversations', JSON.stringify(current))
    return current
  } catch { return [] }
}

function saveConversations(): void {
  localStorage.setItem('llm-wiki-public-conversations', JSON.stringify(conversations.value))
}

async function ensureConversation(): Promise<string> {
  if (activeID.value) return activeID.value
  const id = await createConversation()
  activeID.value = id
  conversations.value.unshift({ id, createdAt: Date.now(), preview: '新对话' })
  saveConversations()
  return id
}

async function startNew(): Promise<void> {
  if (busy.value) await stopCurrent()
  activeID.value = ''
  messages.value = []
  pageError.value = ''
  showHistory.value = false
  await ensureConversation()
}

async function selectConversation(id: string): Promise<void> {
  if (busy.value) return
  pageError.value = ''
  try {
    const history = await loadHistory(id)
    activeID.value = id
    messages.value = history
    showHistory.value = false
    await scrollToBottom()
  } catch {
    conversations.value = conversations.value.filter(item => item.id !== id)
    saveConversations()
    if (activeID.value === id) { activeID.value = ''; messages.value = [] }
  }
}

async function removeConversation(id: string): Promise<void> {
  try { await deleteConversation(id) } catch { /* local expiry is still removed */ }
  conversations.value = conversations.value.filter(item => item.id !== id)
  saveConversations()
  if (activeID.value === id) { activeID.value = ''; messages.value = [] }
}

async function send(): Promise<void> {
  const text = query.value.trim()
  if (!text || !canSend.value) return
  pageError.value = ''
  lastQuestion.value = text
  query.value = ''
  const id = await ensureConversation()
  messages.value.push({ role: 'user', content: text }, { role: 'assistant', content: '', streaming: true, citations: [] })
  const target = messages.value[messages.value.length - 1]
  const saved = conversations.value.find(item => item.id === id)
  if (saved) saved.preview = text.slice(0, 36)
  saveConversations()
  busy.value = true
  currentTurn.value = ''
  requestController.value = new AbortController()
  await scrollToBottom()
  try {
    await streamQuestion(id, text, {
      turn(data) { currentTurn.value = data.turn_id },
      delta(data) { target.content += data.text; void scrollToBottom() },
      citations(data) { target.citations = data.items; target.contains_draft = data.contains_draft; target.contains_unreviewed = data.contains_unreviewed },
      done(data) { target.streaming = false; target.stopped = data.stopped; target.contains_draft ||= data.contains_draft; target.contains_unreviewed ||= data.contains_unreviewed },
      error(data) { target.streaming = false; target.error = data.message },
    }, requestController.value.signal)
  } catch (error) {
    if ((error as Error).name !== 'AbortError') target.error = (error as Error).message
  } finally {
    target.streaming = false
    busy.value = false
    requestController.value = null
    currentTurn.value = ''
  }
}

async function stopCurrent(): Promise<void> {
  if (!busy.value) return
  if (activeID.value && currentTurn.value) {
    try { await stopTurn(activeID.value, currentTurn.value) } catch { /* disconnect path also requests stop */ }
  }
  requestController.value?.abort()
  const target = messages.value.at(-1)
  if (target?.role === 'assistant') { target.streaming = false; target.stopped = true }
  busy.value = false
}

async function retryLast(): Promise<void> {
  if (!lastQuestion.value || busy.value) return
  query.value = lastQuestion.value
  await send()
}

async function openCitation(citation: Citation | string): Promise<void> {
  const slug = typeof citation === 'string' ? citation : citation.slug
  drawerBusy.value = true
  drawer.value = null
  try { drawer.value = await loadCitation(slug) }
  catch (error) { pageError.value = (error as Error).message }
  finally { drawerBusy.value = false }
}

function onAnswerClick(event: MouseEvent): void {
  const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href^="/qa/v1/citations/"]')
  if (!link) return
  event.preventDefault()
  const slug = decodeURIComponent(link.pathname.replace('/qa/v1/citations/', ''))
  void openCitation(slug)
}

function onComposerKey(event: KeyboardEvent): void {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() }
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  messagePane.value?.scrollTo({ top: messagePane.value.scrollHeight, behavior: 'smooth' })
}
</script>

<template>
  <div class="observatory-shell">
    <header class="topbar">
      <button class="brand" aria-label="返回问答首页" @click="startNew">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span>
        <span><strong>深空知识问答</strong><small>EVIDENCE STACK</small></span>
      </button>
      <div class="release-chip"><span></span><b>50 PAGE PLATE</b><small>当前固定知识快照</small></div>
      <div class="top-actions">
        <button class="ghost-button mobile-history" @click="showHistory = !showHistory">观测记录</button>
        <button class="ghost-button primary-action" @click="startNew">开始新问题</button>
      </div>
    </header>

    <div class="workspace">
      <aside class="history-rail" :class="{ open: showHistory }" aria-label="最近七天对话">
        <div class="rail-heading"><span>最近观测</span><small>7 DAYS</small></div>
        <div v-for="item in conversations" :key="item.id" class="history-entry" :class="{ active: item.id === activeID }">
          <button class="history-select" @click="selectConversation(item.id)">
            <span class="history-star" aria-hidden="true"></span>
            <span class="history-copy"><strong>{{ item.preview }}</strong><small>{{ new Date(item.createdAt).toLocaleDateString('zh-CN') }}</small></span>
          </button>
          <button class="history-delete" aria-label="删除对话" @click="removeConversation(item.id)">×</button>
        </div>
        <div v-if="!conversations.length" class="rail-empty"><strong>还没有观测记录</strong><span>提出第一个问题后，记录会在这里保留 7 天。</span></div>
        <div class="rail-policy">
          <span>SESSION / ANONYMOUS</span>
          <p>无需账号。Cookie 只用于隔离你的问答记录。</p>
        </div>
      </aside>

      <main class="chat-stage">
        <section ref="messagePane" class="message-pane" aria-live="polite">
          <div v-if="!messages.length" class="welcome-block">
            <div class="hero-grid">
          <div class="hero-copy">
            <p class="eyebrow">READ-ONLY WIKI · DEEP-SKY</p>
            <h1>先对准证据，<br><em>再开始曝光。</em></h1>
            <p class="welcome-copy">输入一个深空摄影问题。我会搜索并读取当前 50 个 Wiki 页面，将答案与实际引用一起交给你；证据不足时直接说明。</p>
            <div class="knowledge-line" aria-label="知识库范围">
              <span><b>50</b> 稳定页面</span>
              <span><b>Wiki</b> 唯一检索</span>
              <span class="review-state"><b>50</b> 待人工核验</span>
            </div>
              </div>

              <div class="exposure-stack" aria-label="快速提问">
                <span class="subframe frame-back" aria-hidden="true"></span>
                <span class="subframe frame-mid" aria-hidden="true"></span>
                <div class="exposure-plate">
              <div class="plate-edge"><span>STACK / 50 PAGES</span><span>READ ONLY</span></div>
                  <div class="finder-field">
                    <span class="crosshair horizontal" aria-hidden="true"></span>
                    <span class="crosshair vertical" aria-hidden="true"></span>
                    <i class="plate-star ps-a" aria-hidden="true"></i><i class="plate-star ps-b" aria-hidden="true"></i><i class="plate-star ps-c" aria-hidden="true"></i><i class="plate-star ps-d" aria-hidden="true"></i>
                    <div class="finder-copy">
                      <p>从一个真实场景开始</p>
                      <div class="prompt-suggestions">
                        <button @click="query = '城市阳台第一次拍深空，应该先做什么？'; send()"><small>现场拍摄</small><strong>城市阳台首拍怎么开始？</strong><span>→</span></button>
                        <button @click="query = 'Siril 新手从原片到首图的流程是什么？'; send()"><small>后期处理</small><strong>Siril 从原片到首图</strong><span>→</span></button>
                      </div>
                    </div>
                  </div>
                  <div class="plate-edge"><span>NO WEB / NO EMBEDDING</span><span>REVIEW PENDING</span></div>
                </div>
              </div>
            </div>
            <p class="hero-footnote"><span>Hα</span> 页面是证据，不是指令。你可以打开每张引用卡，回到完整原页核验。</p>
          </div>

          <article v-for="(message, index) in messages" :key="index" class="message" :class="message.role">
            <div class="message-meta"><span>{{ message.role === 'user' ? '观测问题' : '证据叠加结果' }}</span><small>{{ message.streaming ? '正在读取 Wiki 页面…' : '' }}</small></div>
            <div v-if="message.contains_draft" class="draft-warning"><span>草稿知识</span> 本回答包含尚未经最终审核的公开候选内容</div>
            <div v-else-if="message.contains_unreviewed" class="draft-warning"><span>待人工核验</span> 本回答依据稳定页面，但尚无人工 verified 记录</div>
            <div v-if="message.streaming" class="streaming-copy">{{ message.content }}<span class="cursor"></span></div>
            <div v-else-if="message.role === 'assistant'" class="markdown-body" @click="onAnswerClick" v-html="renderMarkdown(message.content)"></div>
            <div v-else class="user-copy">{{ message.content }}</div>
            <div v-if="message.error" class="message-error">{{ message.error }} <button @click="retryLast">重试</button></div>
            <div v-if="message.stopped" class="stopped-note">生成已停止</div>
            <div v-if="message.citations?.length" class="citation-strip">
              <div class="citation-heading"><span>已读取的证据页</span><small>{{ message.citations.length }} PAGES</small></div>
              <button v-for="(citation, cIndex) in message.citations" :key="citation.slug" class="citation-card" @click="openCitation(citation)">
                <span class="citation-index">{{ String(cIndex + 1).padStart(2, '0') }}</span>
                <span><strong>{{ citation.title }}</strong><small>{{ citation.is_draft ? '草稿 · 未最终审核' : citation.needs_review ? '稳定 · 待人工核验' : '人工已核验' }}</small></span>
                <span aria-hidden="true">↗</span>
              </button>
            </div>
          </article>
        </section>

        <div v-if="pageError" class="page-error">{{ pageError }}</div>
        <section class="composer-wrap">
          <div class="composer-label"><span>输入观测问题</span><small>{{ query.length }} / 2000</small></div>
          <div class="composer" :class="{ busy }">
            <textarea v-model="query" maxlength="2000" rows="1" placeholder="例如：城市阳台首拍时，怎么选目标？" aria-label="问题" @keydown="onComposerKey"></textarea>
            <button v-if="busy" class="stop-button" aria-label="停止生成" @click="stopCurrent"><span></span>停止</button>
            <button v-else class="send-button" :disabled="!canSend" aria-label="发送问题" @click="send">提问</button>
          </div>
          <p class="composer-foot"><span>配额：3 次 / 分钟 · 最长 120 秒</span><span>AI 会出错，重要结论请打开引用页核验。</span></p>
        </section>
      </main>
    </div>

    <div v-if="drawer || drawerBusy" class="drawer-scrim" @click.self="drawer = null">
      <aside class="citation-drawer" aria-label="引用原文">
        <button class="drawer-close" aria-label="关闭引用" @click="drawer = null">×</button>
        <div v-if="drawerBusy" class="drawer-loading">正在读取公开页面…</div>
        <template v-else-if="drawer">
          <p class="eyebrow">EVIDENCE PAGE / {{ drawer.release_id }}</p>
          <div v-if="drawer.is_draft" class="draft-warning"><span>草稿</span> 尚未经最终审核</div>
          <div v-else-if="drawer.needs_review" class="draft-warning"><span>待人工核验</span> 页面为 stable，但尚无人工 verified 记录</div>
          <h2>{{ drawer.title }}</h2>
          <p class="drawer-summary">{{ drawer.summary }}</p>
          <div class="drawer-meta"><span>{{ drawer.source_status }}</span><span>{{ drawer.source_access }}</span></div>
          <div class="markdown-body source-content" v-html="renderMarkdown(drawer.content)" @click="onAnswerClick"></div>
        </template>
      </aside>
    </div>
  </div>
</template>
