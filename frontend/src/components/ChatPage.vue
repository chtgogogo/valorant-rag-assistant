<template>
   <div class="app-layout">
     <!-- 知识库侧边栏 -->
     <aside class="kb-sidebar" :class="{ open: sidebarOpen }">
       <div class="sidebar-header">
         <div class="sidebar-logo">
           <svg viewBox="0 0 40 40" class="mini-logo">
             <polygon points="20,4 36,12 36,28 20,36 4,28 4,12" fill="none" stroke="currentColor" stroke-width="2"/>
             <text x="20" y="26" text-anchor="middle" fill="currentColor" font-weight="900" font-size="16" font-style="italic">V</text>
           </svg>
         </div>
         <span class="sidebar-title">知识库</span>
         <button class="sidebar-close" @click="sidebarOpen = false"><svg class="close-icon" viewBox="0 0 16 16"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg></button>
       </div>
       <div class="sidebar-categories" v-if="currentKb === 'valorant'">
         <div class="cat-section" v-for="cat in kbCategories" :key="cat.name">
           <div class="cat-header" @click="cat.open = !cat.open">
 <span class="cat-icon" v-html="cat.icon"></span>
             <span class="cat-name">{{ cat.name }}</span>
             <span class="cat-arrow" :class="{ rotated: cat.open }"><svg class="chevron-icon" viewBox="0 0 16 16"><path d="M6 4l4 4-4 4" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
           </div>
           <div class="cat-items" v-show="cat.open">
             <div class="cat-item" v-for="item in cat.items" :key="item.label" @click="quickAsk(item.label)">
               <span class="item-label">{{ item.label }}</span>
               <span class="item-badge" v-if="item.badge">{{ item.badge }}</span>
             </div>
           </div>
         </div>
       </div>
       <div class="sidebar-categories" v-else>
         <div class="domain-info-card">
           <div class="info-title">{{ currentAppName }}</div>
           <p class="info-desc">点击下方问题快速提问，也可以直接输入。</p>
         </div>
         <div class="cat-section">
           <div
             class="cat-item"
             v-for="q in quickQuestions"
             :key="q.label"
             @click="quickAsk(q.text)"
           >
             <span class="item-label">{{ q.label }}</span>
           </div>
         </div>
       </div>
       <div class="sidebar-footer">
         <div class="connection-status" :class="{ online: backendOnline }">
           <span class="status-dot"></span>
           <span class="status-text">{{ backendOnline ? '已连接' : '离线' }}</span>
         </div>
       </div>
     </aside>
 
     <div class="chat-container" :class="{ sidebarActive: sidebarOpen }">
      <!-- 移动端侧栏遮罩：点空白处收起侧栏（v3.28） -->
      <div class="sidebar-backdrop" v-if="sidebarOpen" @click="sidebarOpen = false"></div>
       <!-- 动态背景 -->
       <div class="bg-gradient-wpr">
         <div class="bg-gradient"></div>
       </div>
    <div class="bg-particles">
      <div class="particle" :class="{ teal: n % 4 === 0 }" v-for="n in 20" :key="n" :style="particleStyle(n)"></div>
    </div>

    <!-- 顶部标题栏 -->
       <header class="chat-header">
         <div class="header-brand">
           <button class="menu-toggle" @click="sidebarOpen = !sidebarOpen" title="知识库">
             <svg viewBox="0 0 24 24" class="menu-icon"><path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
           </button>
           <div class="logo-wrap">
             <svg class="logo-svg" viewBox="0 0 40 40">
               <polygon points="20,4 36,12 36,28 20,36 4,28 4,12" fill="none" stroke="currentColor" stroke-width="2"/>
               <text x="20" y="26" text-anchor="middle" fill="currentColor" font-weight="900" font-size="16" font-style="italic">V</text>
             </svg>
           </div>
           <div class="brand-text">
             <h1>{{ currentAppName }}</h1>
             <p>基于 RAG 的多领域智能问答 · 当前知识库：{{ currentKb }}</p>
           </div>
         </div>
         <div class="header-tools">
           <div class="domain-switch" title="一键切换知识库 / 领域">
             <button
               v-for="d in domains"
               :key="d.domain"
               class="domain-btn"
               :class="{ active: currentKb === d.domain }"
               @click="switchDomain(d)"
             >{{ d.short_name || d.app_name }}</button>
           </div>
           <div class="connection-badge" :class="{ connected: backendOnline }">
             <span class="badge-dot"></span>
             <span class="badge-label">{{ backendOnline ? '已连接' : '未连接' }}</span>
           </div>
            <button class="tool-btn tool-btn-text" @click="emit('manage', currentKb)" title="知识库管理">知识库</button>

           <button class="tool-btn" @click="handleClear" :disabled="loading" title="清空对话">
             <svg viewBox="0 0 24 24" class="tool-icon"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
           </button>
           <button class="tool-btn" @click="handleRollback" :disabled="loading || messages.length === 0" title="撤回">
             <svg viewBox="0 0 24 24" class="tool-icon"><path d="M4 17l6-6-6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/><path d="M10 11h7a4 4 0 010 8h-1" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round"/></svg>
           </button>
         </div>
       </header>

    <!-- 消息区域 -->
    <main class="chat-main" ref="messagesContainer">
      <!-- 欢迎屏 -->
      <transition name="fade">
        <div v-if="messages.length === 0" class="welcome-area">
          <div class="welcome-hero">
            <svg class="hero-icon" viewBox="0 0 100 100">
              <polygon points="50,8 88,28 88,72 50,92 12,72 12,28" fill="none" stroke="#ff4655" stroke-width="2.5"/>
              <text x="50" y="62" text-anchor="middle" fill="#ff4655" font-weight="900" font-size="36" font-style="italic">V</text>
            </svg>
            <h2 class="hero-title">{{ currentAppName }}</h2>
            <p class="hero-desc">{{ currentKb === 'valorant' ? '随时问我英雄技能、武器属性、地图策略，秒回答案' : '退换货、价保、物流、三包等售后问题直接问；答不上的会自动转人工工单' }}</p>
          </div>
          <div class="quick-grid">
            <div
              class="quick-card"
              v-for="(q, idx) in quickQuestions"
              :key="idx"
              :style="{ animationDelay: idx * 0.1 + 's' }"
               @click="quickAsk(q.text)"
            >
 <span class="q-emoji" v-html="q.emoji"></span>
               <span class="q-text">{{ q.label }}</span>
            </div>
          </div>
        </div>
      </transition>

      <!-- 聊天消息 -->
      <transition-group name="msg" tag="div" class="messages-list">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="msg-row"
          :class="msg.role"
        >
          <div class="msg-avatar" :class="msg.role">
            <template v-if="msg.role === 'assistant'">
              <img class="avatar-img" src="/sage-avatar.png" alt="Sage" />
            </template>
            <template v-else>
              <img class="avatar-img" src="/iso-avatar.png" alt="Iso" />
            </template>
          </div>
          <div class="msg-body">
            <div class="msg-bubble" :class="[msg.role, { error: msg.isError }]">
              <div v-if="msg.role === 'assistant'" class="md-body" v-html="msg.html"></div>
              <div v-else class="user-text">{{ msg.content }}</div>
            </div>
            <!-- 用户反馈（v3.8）：assistant 回复下点赞/点踩；已评价置灰防重评（组件内存状态，不持久化）；错误气泡不参与评价 -->
            <div v-if="msg.role === 'assistant' && msg.content && !loading && !msg.isError" class="msg-feedback">
              <button class="fb-btn" :disabled="!!msg.fb" :class="{ active: msg.fb === 'up' }" @click="handleFeedback(msg, 'up')" title="有帮助">👍</button>
              <button class="fb-btn" :disabled="!!msg.fb" :class="{ active: msg.fb === 'down' }" @click="handleFeedback(msg, 'down')" title="没帮助">👎</button>
            </div>
            <div v-if="msg.sources && msg.sources.length > 0" class="msg-footnotes">
              <span class="fn-label">参考</span>
              <span class="fn-tag" v-for="src in msg.sources" :key="src.id">{{ src.name }}<span v-if="src.score" class="fn-score"> {{ Math.round(src.score * 100) }}%</span></span>
            </div>
            <div class="msg-time">{{ msg.time }}</div>
          </div>
        </div>
      </transition-group>

      <!-- 加载动画 -->
      <div v-if="loading" class="msg-row assistant">
        <div class="msg-avatar assistant">
          <svg viewBox="0 0 32 32" class="avatar-svg">
            <polygon points="16,4 28,10 28,22 16,28 4,22 4,10" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <text x="16" y="21" text-anchor="middle" fill="currentColor" font-weight="900" font-size="12" font-style="italic">V</text>
          </svg>
        </div>
        <div class="msg-body">
          <div class="msg-bubble assistant typing-bubble">
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
            <span class="typing-dot"></span>
          </div>
        </div>
      </div>
    </main>

       <!-- 底部输入区 -->
    <footer class="chat-footer">
      <div class="input-wrapper">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="msg-input"
          placeholder="输入问题（最多 100 字）..."
          rows="1"
          maxlength="100"
          @keydown.enter.exact="onEnterKey"
          @input="autoResize"
          :disabled="loading"
        ></textarea>
        <span class="input-counter" v-if="inputText.length > 60">{{ inputText.length }}/100</span>
        <button
          class="send-btn"
          @click="handleSend"
          :disabled="!inputText.trim() || loading"
          :class="{ active: inputText.trim() && !loading }"
        >
          <svg v-if="!loading" viewBox="0 0 24 24" class="send-icon">
            <path d="M2 21L23 12L2 3V10L17 12L2 14V21Z" fill="currentColor"/>
          </svg>
          <span v-else class="loading-spinner"></span>
        </button>
     </div>
    </footer>
   </div>
 </div>
 </template>

<script setup>
import { ref, reactive, nextTick, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Marked } from 'marked'
import DOMPurify from 'dompurify'
import { sendMessage, sendMessageStream, clearHistory, rollbackHistory, getHistory, testChat, getDomains, sendFeedback } from '../api.js'

const emit = defineEmits(['manage'])


// ---- 状态 ----
 const messages = ref([])
 const sidebarOpen = ref(window.innerWidth > 768)
const inputText = ref('')
const loading = ref(false)
const backendOnline = ref(false)
const messagesContainer = ref(null)
const inputRef = ref(null)

// ---- 领域切换（v3.6）：kb_id 即领域键，提示词/话术/快捷问题整套跟随 ----
const domains = ref([])
const currentKb = ref('valorant')
const currentProfile = computed(() =>
  domains.value.find((d) => d.domain === currentKb.value) || null
)
const currentAppName = computed(
  () => currentProfile.value?.app_name || '智能问答'
)
async function switchDomain(d) {
  if (d.domain === currentKb.value) return
  currentKb.value = d.domain
  // 跨域对话上下文不通用：清空当前会话
  messages.value = []
  try { await clearHistory(sessionId) } catch { /* 静默 */ }
  ElMessage.success(`已切换到「${d.app_name}」知识库`)
}

let msgCounter = 0
// 会话ID持久化：刷新页面复用同一 session_id，对话历史不丢
const SESSION_KEY = 'valorant_session_id'
let sessionId = localStorage.getItem(SESSION_KEY)
if (!sessionId) {
  sessionId = 'web_' + Date.now()
  localStorage.setItem(SESSION_KEY, sessionId)
}

 const kbCategories = ref([
   {
     name: '英雄指南', icon: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M14 9l-2 2m0 0l-4 4m4-4l2 2m-2-2l-2-2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>', open: true,
     items: [
       { label: '捷风(Jett)怎么玩？', badge: '决斗者' },
       { label: '不死鸟(Phoenix)技能', badge: '决斗者' },
       { label: '芮娜(Reyna)攻略', badge: '决斗者' },
       { label: '幽影(Omen)传送技巧', badge: '控场者' },
       { label: '贤者(Sage)怎么玩？', badge: '哨位' },
       { label: '猎枭(Sova)侦察技巧', badge: '先锋' },
       { label: '蝰蛇(Viper)毒雾控制', badge: '控场者' },
       { label: '全部英雄列表', badge: '汇总' },
     ]
   },
   {
     name: '武器图鉴', icon: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><rect x="3" y="10" width="12" height="4" rx="1" stroke="currentColor" stroke-width="2"/><path d="M15 12h4l3-3v6l-3-3h-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>', open: false,
     items: [
       { label: '幻影和狂徒对比', badge: '步枪' },
       { label: '冥狙狙击技巧', badge: '狙击' },
       { label: '哪些手枪值得买？', badge: '手枪' },
       { label: '经济局怎么买装备？', badge: '策略' },
       { label: '全部武器属性', badge: '汇总' },
     ]
   },
   {
     name: '地图攻略', icon: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M9 3L3 9l6 6-6 6 18 0" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="15" cy="7" r="2" stroke="currentColor" stroke-width="2"/></svg>', open: false,
     items: [
       { label: '源工重镇怎么打？', badge: '地图' },
       { label: '隐士修所攻略', badge: '地图' },
       { label: '亚海悬城点位', badge: '地图' },
       { label: '森寒冬港进攻路线', badge: '地图' },
       { label: '所有地图汇总', badge: '汇总' },
     ]
   },
   {
     name: '新手入门', icon: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" stroke-width="2"/><path d="M9 10h.01M15 10h.01" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M9 15a3 3 0 006 0H9z" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>', open: false,
     items: [
       { label: '游戏基础规则', badge: '入门' },
       { label: '经济系统全解', badge: '进阶' },
       { label: '新手选什么英雄？', badge: '推荐' },
       { label: '射击技巧和训练', badge: '技巧' },
       { label: '上分分段攻略', badge: '攻略' },
     ]
   }
 ])
 
const VALORANT_QUICK = [
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M14 9l-2 2m0 0l-4 4m4-4l2 2m-2-2l-2-2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',  label: '捷风怎么玩?', text: '捷风怎么玩？' },
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><rect x="3" y="10" width="12" height="4" rx="1" stroke="currentColor" stroke-width="2"/><path d="M15 12h4l3-3v6l-3-3h-4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',  label: '狂徒 vs 幻影', text: '狂徒和幻影哪个好？' },
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M9 3L3 9l6 6-6 6 18 0" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="15" cy="7" r="2" stroke="currentColor" stroke-width="2"/></svg>',  label: '源工重镇地图攻略', text: '源工重镇地图怎么打？' },
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" stroke="currentColor" stroke-width="2"/><path d="M9 10h.01M15 10h.01" stroke="currentColor" stroke-width="3" stroke-linecap="round"/><path d="M9 15a3 3 0 006 0H9z" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',  label: '新手英雄推荐', text: '新手适合用什么英雄？' },
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M12 2a7 7 0 00-7 7c0 2.4 1.2 4.5 3 5.7V18a2 2 0 002 2h4a2 2 0 002-2v-3.3c1.8-1.2 3-3.3 3-5.7a7 7 0 00-7-7z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M10 21h4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',  label: '游戏机制讲解', text: '游戏基本机制是什么？' },
 { emoji: '<svg class="icon-cat" viewBox="0 0 24 24" fill="none"><path d="M12 2a10 10 0 00-10 10v8l3-3 3 3 3-3 3 3 3-3 3 3v-8a10 10 0 00-10-10z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="9" cy="11" r="1" fill="currentColor" opacity="0.5"/><circle cx="15" cy="11" r="1" fill="currentColor" opacity="0.5"/></svg>',  label: '幽影技能介绍', text: '幽影技能介绍' },
]

// 当前领域的快捷问题：领域配置有 quick_questions 用之（如电商售后），否则回退游戏默认
const quickQuestions = computed(() => {
  const fromDomain = currentProfile.value?.quick_questions || []
  if (fromDomain.length) {
    // 【v3.13】emoji 来自后端领域配置（动态内容）且经 v-html 渲染，同样过 DOMPurify；
    // 领域配置现均为纯文本表情（📦 等），净化后渲染结果不变
    return fromDomain.map((q) => ({ emoji: DOMPurify.sanitize(q.emoji || ''), label: q.label, text: q.text }))
  }
  return VALORANT_QUICK
})
 
 function quickAsk(text) {
   inputText.value = text
   handleSend()
 }

// ---- Markdown 渲染（marked v18 API） ----
const mdParser = new Marked({
  breaks: true,
  gfm: true,
})

function renderMarkdown(text) {
  try {
    // marked v18: parse() 返回 Promise，但同步调用时立即返回
    const raw = mdParser.parse(text) || text
    // 【v3.13 安全修复】渲染内容 = LLM 输出 + 知识库文档（文档可被任何人上传），
    // marked 本身不做 HTML 净化，<img onerror>/<script>/javascript: 会原样进 v-html。
    // 统一在进 v-html 前用 DOMPurify 净化：剥离事件属性、script、危险协议，保留正常 markdown 标签。
    return DOMPurify.sanitize(raw)
  } catch {
    return text.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}

// ---- 菜单函数 ----
function getTime() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

function autoResize() {
  nextTick(() => {
    const el = inputRef.value
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 160) + 'px'
    }
  })
}

// 【v3.28】回车键分设备：桌面=发送，手机虚拟键盘=换行（发送靠按钮，符合移动端聊天习惯）。
// 手机端 maxlength=100 与后端 MAX_QUESTION_CHARS=100 对齐，超发会被后端拒绝。
const isTouch = window.matchMedia('(pointer: coarse)').matches

function onEnterKey(e) {
  if (isTouch) return // 不 preventDefault，保留换行
  e.preventDefault()
  handleSend()
}

// ---- 背景粒子样式 ----
function particleStyle(n) {
  const left = ((n * 37 + 13) % 100)
  const delay = (n * 0.7) % 8
  const duration = 8 + (n % 6)
  const size = 2 + (n % 3)
  return {
    left: left + '%',
    animationDelay: delay + 's',
    animationDuration: duration + 's',
    width: size + 'px',
    height: size + 'px',
  }
}

// ---- 发送消息 ----
async function handleSend() {
  const question = inputText.value.trim()
  if (!question || loading.value) return

  messages.value.push({
    id: ++msgCounter,
    role: 'user',
    content: question,
    html: '',
    sources: [],
    time: getTime(),
  })

  inputText.value = ''
  autoResize()
  loading.value = true
  scrollToBottom()

  // v3.0：优先走流式接口，答案逐字渲染；流式不可用时降级为普通请求
  try {
    const placeholder = reactive({
      id: ++msgCounter,
      role: 'assistant',
      content: '',
      html: '',
      sources: [],
      time: getTime(),
    })
    let started = false // 收到首个token后才把占位消息上屏，避免空泡闪烁

    const { answer } = await sendMessageStream(sessionId, question, currentKb.value, {
      onSources: (sources) => { placeholder.sources = sources || [] },
      onToken: (delta) => {
        if (!started) {
          messages.value.push(placeholder)
          started = true
        }
        placeholder.content += delta
        placeholder.html = renderMarkdown(placeholder.content)
        scrollToBottom()
      },
    })
    // 兜底：全程没收到任何token（如直接done），也要保证答案上屏
    if (!started) {
      placeholder.content = answer
      placeholder.html = renderMarkdown(answer)
      messages.value.push(placeholder)
    }
  } catch (streamErr) {
    // v3.10：LLM 生成失败（后端 error 事件）/ SSE 连接中断 → 渲染错误气泡并停止 loading，
    // 不再降级走普通接口重问（避免二次撞限流继续转圈）
    if (streamErr && (streamErr.isLlmError || streamErr.isStreamInterrupted)) {
      const tip = streamErr.llmMessage || '模型服务繁忙，请稍后再试'
      messages.value.push({
        id: ++msgCounter,
        role: 'assistant',
        content: tip,
        html: renderMarkdown(tip),
        sources: [],
        time: getTime(),
        isError: true,
      })
    } else {
    // 流式失败（后端未升级/网络断开）→ 降级走原来的普通接口
    try {
      const res = await sendMessage(sessionId, question)
      const data = res.data

      if (data.code === 200) {
        messages.value.push({
          id: ++msgCounter,
          role: 'assistant',
          content: data.data.answer,
          html: renderMarkdown(data.data.answer),
          sources: data.data.sources || [],
          time: getTime(),
        })
      } else {
        messages.value.push({
          id: ++msgCounter,
          role: 'assistant',
          content: data.msg || '请求失败',
          html: renderMarkdown(data.msg || '请求失败'),
          sources: [],
          time: getTime(),
        })
      }
    } catch {
      messages.value.push({
        id: ++msgCounter,
        role: 'assistant',
        content: '网络错误，请检查后端服务是否启动',
        html: '<p>网络错误，请检查后端服务是否启动</p>',
        sources: [],
        time: getTime(),
      })
    }
    }
  } finally {
    loading.value = false
    scrollToBottom()
  }
}

async function handleClear() {
  try {
    await ElMessageBox.confirm('确定清空所有对话记录吗？', '清空确认', { type: 'warning' })
    await clearHistory(sessionId)
    messages.value = []
    ElMessage.success('对话已清空')
  } catch { /* 取消 */ }
}

// ---- 用户反馈（v3.8）：对 assistant 回复点赞/点踩 ----
// 问题取该回复前最近一条 user 消息（问题+答案成对落库，供 badcase 回流）
function findQuestion(msg) {
  const idx = messages.value.indexOf(msg)
  for (let i = idx - 1; i >= 0; i--) {
    if (messages.value[i].role === 'user') return messages.value[i].content
  }
  return ''
}

// 【W8-卡6】最近 ≤3 轮完整对话（含本轮，每轮 user+assistant 两条）：坏例分析时看"之前问了什么导致这次答歪"
function buildContext(msg) {
  const idx = messages.value.indexOf(msg)
  return messages.value
    .slice(Math.max(0, idx - 5), idx + 1)
    .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content && !m.isError)
    .map((m) => ({ role: m.role, content: m.content }))
}

async function handleFeedback(msg, rating) {
  if (msg.fb) return // 已评价过，防重评（组件内存状态，不做持久化）
  msg.fb = rating // 先本地置灰再请求，防连点重复提交
  try {
    // 【W8-卡6】meta：领域即 kb_id；回答路径当前恒为 workflow（Agent 路径上线后按消息来源标注）；trace_id 预留关联 Agent 轨迹
    await sendFeedback(sessionId, findQuestion(msg), msg.content, rating, buildContext(msg), {
      domain: currentKb.value,
      path: 'workflow',
      trace_id: null,
    })
    ElMessage.success('感谢反馈')
  } catch {
    msg.fb = null // 提交失败回滚状态，允许重试
    ElMessage.error('反馈提交失败')
  }
}

async function handleRollback() {
  try {
    const turnIndex = Math.max(0, messages.value.length - 2)
    const res = await rollbackHistory(sessionId, turnIndex)
    if (res.data.code === 200) {
      const history = res.data.data.history || []
      msgCounter = 0
      messages.value = history.map(msg => ({
        id: ++msgCounter,
        role: msg.role,
        content: msg.content,
        html: msg.role === 'assistant' ? renderMarkdown(msg.content) : '',
        sources: [],
        time: getTime(),
      }))
      ElMessage.success('已撤回一轮')
    } else {
      ElMessage.error(res.data.msg || '撤回失败')
    }
  } catch (err) {
    ElMessage.error('撤回失败')
  }
}

 onMounted(() => {
   const updateSidebar = () => { sidebarOpen.value = window.innerWidth > 768 }
   window.addEventListener('resize', updateSidebar)
   testChat().then(r => { backendOnline.value = r.data.code === 200 }).catch(() => { backendOnline.value = false })
   // 拉取可用领域列表：一键切换知识库（v3.6）
   getDomains().then((r) => {
     const data = r.data.data || {}
     domains.value = data.domains || []
     if (data.default) currentKb.value = data.default
   }).catch(() => { /* 领域接口不可用时保持默认域 */ })
   // 刷新恢复：按持久化的 session_id 拉取历史消息并渲染（结构与 handleRollback 恢复逻辑一致）
   getHistory(sessionId).then((r) => {
     const history = r.data?.data?.history || []
     if (!history.length) return
     msgCounter = 0
     messages.value = history.map((msg) => ({
       id: ++msgCounter,
       role: msg.role,
       content: msg.content,
       html: msg.role === 'assistant' ? renderMarkdown(msg.content) : '',
       sources: [],
       time: getTime(),
     }))
     scrollToBottom()
   }).catch(() => { /* 历史接口不可用时静默，保持空列表 */ })
 })
</script>

<style scoped>
/* ============ CUSTOM SVG ICONS ============ */
.icon-cat {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: inherit;
}

.chevron-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: inherit;
  transition: transform 0.25s;
}

.close-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: inherit;
}

.cat-icon .icon-cat {
  color: var(--val-text-dim);
}

.q-emoji .icon-cat {
  width: 18px;
  height: 18px;
  color: var(--val-text-dim);
}

.quick-card:hover .q-emoji .icon-cat {
  color: var(--val-red);
}

/* ============ VALORANT DARK THEME - REFINED ============ */

:root {
  --val-red: #ff4655;
  --val-red-dim: rgba(255, 70, 85, 0.15);
  --val-bg-base: #0a0f16;
  --val-bg-surface: #0f1923;
  --val-bg-elevated: #1a2735;
  --val-bg-hover: rgba(255, 255, 255, 0.04);
  --val-border: rgba(100, 135, 160, 0.2);
  --val-text: #e2e8f0;
  --val-text-dim: #a0bfd0;
  --val-text-muted: #6080a0;
  --val-accent: #4ade80;
  --val-radius-sm: 6px;
  --val-radius-md: 10px;
  --val-radius-lg: 14px;
}

/* ============ LAYOUT ============ */
.app-layout {
  height: 100vh;
  height: 100dvh; /* 【v3.28】动态视口：手机地址栏收缩时不再把底部输入框顶出屏幕 */
  display: flex;
  background: #06080a;
  color: var(--val-text); /* 【v3.32.1】根容器兜底浅色文字：此前未声明，侧栏信息卡标题等
                             漏设 color 的文字继承浏览器默认黑色，在深色背景上"漆黑隐身" */
  overflow: hidden;
}

/* 【v3.28】移动端侧栏遮罩（桌面隐藏，≤768 显示） */
.sidebar-backdrop { display: none; }

/* ============ SIDEBAR ============ */
.kb-sidebar {
  width: 280px;
  min-width: 280px;
  background: linear-gradient(180deg, #0d1620 0%, #0a0f16 100%);
  border-right: 1px solid var(--val-border);
  display: flex;
  flex-direction: column;
  transition: width 0.35s cubic-bezier(0.4, 0, 0.2, 1),
              min-width 0.35s cubic-bezier(0.4, 0, 0.2, 1);
  z-index: 100;
  overflow: hidden;
}

.kb-sidebar:not(.open) {
  width: 0;
  min-width: 0;
  border-right: none;
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 18px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.04);
  flex-shrink: 0;
}

.sidebar-logo .mini-logo {
  width: 30px;
  height: 30px;
  color: var(--val-red);
  filter: drop-shadow(0 0 8px rgba(255, 70, 85, 0.3));
}

.sidebar-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--val-text);
  flex: 1;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.sidebar-close {
  background: none;
  border: none;
  color: var(--val-text-muted);
  font-size: 16px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--val-radius-sm);
  transition: all 0.2s;
  line-height: 1;
}

.sidebar-close:hover {
  color: var(--val-text);
  background: var(--val-bg-hover);
}

.sidebar-categories {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
}

.sidebar-categories::-webkit-scrollbar { width: 3px; }
.sidebar-categories::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.06); border-radius: 10px; }

.cat-section { margin: 2px 0; }

.cat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
  border-radius: 0;
}

.cat-header:hover { background: var(--val-bg-hover); }

.cat-icon { font-size: 15px; line-height: 1; }
.cat-name { font-size: 12px; font-weight: 600; color: var(--val-text-dim); flex: 1; letter-spacing: 0.3px; }
.cat-arrow { font-size: 9px; color: var(--val-text-muted); transition: transform 0.25s; }
.cat-arrow.rotated { transform: rotate(90deg); }

.cat-items { padding: 0 6px; }

.cat-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px 7px 34px;
  border-radius: var(--val-radius-sm);
  cursor: pointer;
  transition: all 0.2s var(--ease-out-quart);
  font-size: 12.5px;
  color: var(--val-text-dim);
  border-left: 2px solid transparent;
}

.cat-item:hover {
  background: var(--val-red-dim);
  color: var(--val-text);
  border-left-color: var(--val-red);
  transform: translateX(2px);
}

.item-badge {
  margin-left: auto;
  font-size: 10px;
  padding: 1px 8px;
  clip-path: polygon(5px 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%, 0 5px);
  background: rgba(255, 70, 85, 0.1);
  color: #ff8a95;
  white-space: nowrap;
  font-weight: 500;
  font-family: var(--font-display);
  letter-spacing: 0.5px;
}

.sidebar-footer {
  padding: 12px 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.04);
  flex-shrink: 0;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--val-text-muted);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--val-text-muted);
  transition: all 0.3s;
}

.connection-status.online .status-dot {
  background: var(--val-teal);
  box-shadow: 0 0 8px rgba(24, 229, 182, 0.5);
  animation: statusPulse 2s ease-in-out infinite;
}

@keyframes statusPulse {
  0%, 100% { box-shadow: 0 0 8px rgba(24, 229, 182, 0.3); }
  50% { box-shadow: 0 0 14px rgba(24, 229, 182, 0.6); }
}

.connection-status.online { color: var(--val-teal); }

/* ============ CHAT CONTAINER ============ */
.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #06080a;
  color: var(--val-text);
  font-family: 'Microsoft YaHei', 'PingFang SC', -apple-system, sans-serif;
  position: relative;
  overflow: hidden;
  transition: margin-left 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

/* ============ BACKGROUND EFFECTS ============ */
.bg-gradient-wpr {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}

.bg-gradient {
  position: absolute;
  top: -30%;
  right: -15%;
  width: 70%;
  height: 80%;
  background: radial-gradient(ellipse at center, rgba(255, 70, 85, 0.03) 0%, transparent 65%);
  animation: gradientDrift 14s ease-in-out infinite alternate;
}

@keyframes gradientDrift {
  0% { transform: translate(0, 0) scale(1); opacity: 0.6; }
  50% { transform: translate(-20px, 15px) scale(1.08); opacity: 0.8; }
  100% { transform: translate(-40px, 30px) scale(1.15); opacity: 0.6; }
}

.bg-particles {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}

.particle {
  position: absolute;
  bottom: -10px;
  background: rgba(255, 70, 85, 0.12);
  border-radius: 50%;
  animation: floatUp linear infinite;
}

/* 每第 4 颗为青绿，红青混色更接近官方战术 HUD */
.particle.teal {
  background: rgba(24, 229, 182, 0.1);
}

@keyframes floatUp {
  0%   { transform: translateY(0) scale(0); opacity: 0; }
  8%   { opacity: 0.5; }
  85%  { opacity: 0.08; }
  100% { transform: translateY(-110vh) scale(1.3); opacity: 0; }
}

/* ============ HEADER ============ */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  background: rgba(6, 8, 12, 0.95);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-bottom: 1px solid rgba(255, 70, 85, 0.12);
  position: relative;
  z-index: 10;
  flex-shrink: 0;
}

/* Valorant 标志性底部红渐变细线（战术 HUD 分隔） */
.chat-header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, transparent 0%, rgba(255, 70, 85, 0.55) 18%, rgba(255, 70, 85, 0.15) 55%, transparent 100%);
  pointer-events: none;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.menu-toggle {
  background: none;
  border: none;
  color: var(--val-text-dim);
  cursor: pointer;
  padding: 6px;
  border-radius: var(--val-radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.menu-toggle:hover {
  color: var(--val-text);
  background: var(--val-bg-hover);
}

.menu-icon { width: 18px; height: 18px; }

.logo-wrap {
  width: 38px;
  height: 38px;
}

.logo-svg {
  width: 100%;
  height: 100%;
  color: var(--val-red);
  filter: drop-shadow(0 0 12px rgba(255, 70, 85, 0.35));
}

.brand-text h1 {
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 2px;
  color: var(--val-red);
  line-height: 1.3;
  font-family: var(--font-display);
  text-shadow: 0 0 18px rgba(255, 70, 85, 0.35);
}

.brand-text h1 .accent {
  color: var(--val-text);
  font-weight: 400;
  font-size: 13px;
  margin-left: 3px;
  letter-spacing: 0;
}

.brand-text p {
  font-size: 10.5px;
  color: var(--val-text-muted);
  margin-top: 1px;
  letter-spacing: 0.3px;
}

.header-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ---- 领域一键切换（v3.6）：斜切角战术舱位 ---- */
.domain-switch {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border-radius: 4px;
  background: var(--val-bg, rgba(255, 255, 255, 0.06));
  border: 1px solid rgba(255, 255, 255, 0.08);
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
}

.domain-btn {
  padding: 5px 14px;
  border: none;
  border-radius: 0;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.2s var(--ease-out-quart);
  white-space: nowrap;
}

.domain-btn:hover {
  color: rgba(255, 255, 255, 0.9);
  background: rgba(255, 255, 255, 0.05);
}

.domain-btn.active {
  background: linear-gradient(135deg, #ff4655, #bd3944);
  color: #fff;
  box-shadow: inset 0 0 12px rgba(255, 255, 255, 0.12), 0 2px 10px rgba(255, 70, 85, 0.35);
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}

/* 侧边栏领域信息卡：左红条 + 斜切 */
.domain-info-card {
  margin: 14px 12px;
  padding: 14px 16px;
  clip-path: polygon(0 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%);
  background: linear-gradient(135deg, rgba(255, 70, 85, 0.06), rgba(255, 255, 255, 0.03));
  border: 1px solid rgba(255, 70, 85, 0.15);
}

.domain-info-card .info-title {
  font-size: 15px;
  font-weight: 800;
  margin-bottom: 8px;
  padding-left: 10px;
  border-left: 3px solid var(--val-red);
  line-height: 1.2;
  color: var(--val-text); /* 【v3.32.1】显式浅色：此标题漏设 color 曾继承浏览器默认黑色 */
  letter-spacing: 0.5px;
}

.domain-info-card .info-desc {
  font-size: 12.5px;
  line-height: 1.7;
  color: rgba(255, 255, 255, 0.55);
  margin: 0;
}


.connection-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  clip-path: polygon(6px 0, 100% 0, 100% calc(100% - 6px), calc(100% - 6px) 100%, 0 100%, 0 6px);
  font-size: 11px;
  color: var(--val-text-muted);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(90, 110, 127, 0.2);
  transition: all 0.3s;
}

.connection-badge.connected {
  color: var(--val-teal);
  border-color: rgba(24, 229, 182, 0.25);
  background: rgba(24, 229, 182, 0.05);
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--val-text-muted);
  transition: all 0.3s;
}

.connection-badge.connected .badge-dot {
  background: var(--val-teal);
  box-shadow: 0 0 6px rgba(24, 229, 182, 0.55);
}

.badge-label { font-size: 11px; }

.tool-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 7px;
  border: 1px solid rgba(90, 110, 127, 0.15);
  border-radius: var(--val-radius-sm);
  background: rgba(255, 255, 255, 0.02);
  color: var(--val-text-dim);
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s;
  min-width: 34px;
  min-height: 34px;
}

.tool-btn:hover:not(:disabled) {
  border-color: rgba(255, 70, 85, 0.3);
  color: var(--val-red);
  background: rgba(255, 70, 85, 0.08);
  transform: scale(1.05);
}

.tool-btn:active:not(:disabled) {
  transform: scale(0.95);
}

.tool-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.tool-btn-text {
  padding: 7px 12px;
  min-width: auto;
  font-weight: 600;
}


.tool-icon { width: 15px; height: 15px; }

/* ============ MESSAGES AREA ============ */
.chat-main {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  position: relative;
  z-index: 1;
  scroll-behavior: smooth;
}

.chat-main::-webkit-scrollbar { width: 4px; }
.chat-main::-webkit-scrollbar-track { background: transparent; }
.chat-main::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.05); border-radius: 10px; }
.chat-main::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.1); }

/* ============ WELCOME SCREEN ============ */
.welcome-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 40px 20px 60px;
  position: relative;
}

/* Valorant 战术网格底纹：细线网格 + 中央红光晕，纯 CSS 零资源 */
.welcome-area::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 60% 45% at 50% 32%, rgba(255, 70, 85, 0.07), transparent 70%),
    linear-gradient(rgba(100, 135, 160, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(100, 135, 160, 0.05) 1px, transparent 1px);
  background-size: 100% 100%, 44px 44px, 44px 44px;
  mask-image: radial-gradient(ellipse 75% 65% at 50% 40%, #000 30%, transparent 100%);
  -webkit-mask-image: radial-gradient(ellipse 75% 65% at 50% 40%, #000 30%, transparent 100%);
  pointer-events: none;
}

.welcome-hero {
  text-align: center;
  margin-bottom: 40px;
  position: relative;
}

.hero-icon {
  width: 80px;
  height: 80px;
  margin-bottom: 20px;
  filter: drop-shadow(0 0 30px rgba(255, 70, 85, 0.35));
  animation: heroPulse 4s ease-in-out infinite;
}

@keyframes heroPulse {
  0%, 100% { transform: scale(1); filter: drop-shadow(0 0 30px rgba(255, 70, 85, 0.35)); }
  50% { transform: scale(1.04); filter: drop-shadow(0 0 40px rgba(255, 70, 85, 0.5)); }
}

.hero-title {
  font-size: 27px;
  font-weight: 800;
  background: linear-gradient(135deg, #ff4655 0%, #ff7b85 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 12px;
  letter-spacing: 4px;
  font-family: var(--font-display);
}

/* 标题下战术装饰线：两端斜切块 + 中线 */
.hero-title::after {
  content: '';
  display: block;
  width: min(220px, 60%);
  height: 2px;
  margin: 10px auto 0;
  background: linear-gradient(90deg, transparent, rgba(255, 70, 85, 0.6) 20%, rgba(255, 70, 85, 0.6) 80%, transparent);
  clip-path: polygon(0 0, 100% 0, calc(100% - 4px) 100%, 4px 100%);
}

.hero-desc {
  font-size: 13px;
  color: var(--val-text-dim);
  letter-spacing: 0.3px;
}

.quick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  max-width: 720px;
  width: 100%;
}

.quick-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.015));
  border: 1px solid rgba(90, 110, 127, 0.14);
  clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px);
  cursor: pointer;
  transition: all 0.25s var(--ease-out-quart);
  animation: cardIn 0.5s var(--ease-out-expo) both;
  position: relative;
  overflow: hidden;
}

/* hover 扫光：一道斜向高光掠过（只动 transform） */
.quick-card::after {
  content: '';
  position: absolute;
  top: -40%;
  left: -80%;
  width: 50%;
  height: 180%;
  background: linear-gradient(105deg, transparent, rgba(255, 255, 255, 0.06), transparent);
  transform: skewX(-20deg);
  transition: left 0.55s var(--ease-out-expo);
  pointer-events: none;
}

.quick-card:hover::after {
  left: 130%;
}

.quick-card:hover {
  border-color: rgba(255, 70, 85, 0.35);
  background: linear-gradient(135deg, rgba(255, 70, 85, 0.08), rgba(255, 70, 85, 0.03));
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(255, 70, 85, 0.1);
}

.quick-card:active {
  transform: translateY(0) scale(0.98);
}

@keyframes cardIn {
  from { opacity: 0; transform: translateY(14px); }
  to   { opacity: 1; transform: translateY(0); }
}

.q-emoji { font-size: 20px; flex-shrink: 0; }
.q-text { font-size: 13px; color: var(--val-text-dim); }
.quick-card:hover .q-text { color: var(--val-text); }

/* ============ MESSAGE LIST ============ */
.messages-list {
  max-width: 800px;
  margin: 0 auto;
  padding: 4px 0;
}

.msg-row {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
  animation: msgIn 0.35s cubic-bezier(0.22, 1, 0.36, 1);
}

@keyframes msgIn {
  from { opacity: 0; transform: translateY(16px); }
  to   { opacity: 1; transform: translateY(0); }
}

.msg-row.user { flex-direction: row-reverse; }

/* ============ AVATARS ============ */
.msg-avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--val-radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
}

.msg-avatar.assistant {
  background: rgba(2, 132, 199, 0.1);
  border: 1px solid rgba(2, 132, 199, 0.2);
}

.msg-avatar.user {
  background: rgba(249, 115, 22, 0.1);
  border: 1px solid rgba(249, 115, 22, 0.2);
}

.avatar-svg { width: 36px; height: 36px; }
.avatar-img { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; background: transparent; }
.avatar-letter { font-size: 12px; font-weight: 700; color: var(--val-text-dim); }

/* ============ MESSAGE BUBBLES ============ */
.msg-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-width: 72%;
}

.msg-row.user .msg-body { align-items: flex-end; }

.msg-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
}

.msg-footnotes { display: flex; gap: 5px; flex-wrap: wrap; }

.fn-tag {
  font-size: 10px;
  color: rgba(139, 155, 171, 1);
  background: rgba(74, 109, 140, 0.15);
  border: 1px solid rgba(74, 109, 140, 0.2);
  padding: 2px 9px;
  clip-path: polygon(5px 0, 100% 0, 100% calc(100% - 5px), calc(100% - 5px) 100%, 0 100%, 0 5px);
  font-family: var(--font-display);
  letter-spacing: 0.4px;
}

.fn-score {
  color: var(--val-gold);
  margin-left: 3px;
  font-weight: 600;
}


.msg-time {
  font-size: 10px;
  color: var(--val-text-muted);
  padding: 0 4px;
}

.msg-bubble {
  padding: 12px 16px;
  border-radius: var(--val-radius-lg);
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
  position: relative;
}

.msg-bubble.assistant {
  background: rgba(189, 57, 68, 0.12);
  border: 1px solid rgba(189, 57, 68, 0.2);
  border-top-left-radius: 4px;
  color: #ffffff;
  overflow: hidden;
}

/* AI 气泡左侧战术红条（来源指示） */
.msg-bubble.assistant::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, var(--val-red), rgba(255, 70, 85, 0.15));
}

.msg-bubble.user {
  background: linear-gradient(135deg, #8b1a24 0%, #bd3944 100%);
  border-top-right-radius: 4px;
  color: #ffffff;
  box-shadow: 0 3px 12px rgba(189, 57, 68, 0.2);
}

/* ---- 错误提示气泡（v3.10：LLM 限流/故障降级提示，暗色主题红调描边） ---- */
.msg-bubble.error {
  background: rgba(255, 70, 85, 0.07);
  border: 1px solid rgba(255, 70, 85, 0.4);
  color: #ffb3ba;
}

.user-text { white-space: pre-wrap; }

/* ---- 消息反馈按钮（v3.8 点赞点踩） ---- */
.msg-feedback { display: flex; gap: 6px; padding: 0 4px; }

.fb-btn {
  border: 1px solid rgba(90, 110, 127, 0.2);
  background: rgba(255, 255, 255, 0.02);
  color: var(--val-text-muted);
  font-size: 12px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s;
}

.fb-btn:hover:not(:disabled) {
  color: var(--val-text);
  border-color: rgba(255, 70, 85, 0.3);
  background: rgba(255, 70, 85, 0.08);
}

.fb-btn.active {
  color: var(--val-red);
  border-color: rgba(255, 70, 85, 0.4);
  background: rgba(255, 70, 85, 0.1);
}

.fb-btn:disabled { opacity: 0.35; cursor: not-allowed; }

/* ============ TYPING INDICATOR ============ */
.typing-bubble {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 16px 20px;
}

.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--val-text-dim);
  animation: dotBounce 1.4s infinite ease-in-out;
}

.typing-dot:nth-child(2) { animation-delay: 0.2s; }
.typing-dot:nth-child(3) { animation-delay: 0.4s; }

@keyframes dotBounce {
  0%, 80%, 100% { transform: scale(0.5); opacity: 0.3; }
  40%           { transform: scale(1);   opacity: 1; }
}

/* ============ INPUT AREA ============ */
.chat-footer {
  padding: 14px 24px 16px;
  padding-bottom: calc(16px + env(safe-area-inset-bottom, 0px));
  background: rgba(6, 8, 12, 0.96);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border-top: 1px solid rgba(90, 110, 127, 0.1);
  z-index: 10;
  flex-shrink: 0;
}

.input-counter {
  align-self: flex-end;
  font-size: 11px;
  color: var(--val-text-muted);
  padding: 0 2px 10px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  max-width: 800px;
  margin: 0 auto;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(90, 110, 127, 0.12);
  border-radius: var(--val-radius-md);
  padding: 5px;
  transition: border-color 0.25s, box-shadow 0.25s;
}

.input-wrapper:focus-within {
  border-color: rgba(255, 70, 85, 0.25);
  box-shadow: 0 0 0 3px rgba(255, 70, 85, 0.05), inset 0 0 0 1px rgba(255, 70, 85, 0.05);
}

.msg-input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--val-text);
  font-size: 13.5px;
  line-height: 1.6;
  padding: 9px 10px 9px 12px;
  resize: none;
  outline: none;
  font-family: inherit;
  min-height: 40px;
  max-height: 150px;
}

.msg-input::placeholder { color: var(--val-text-muted); }
.msg-input:disabled { opacity: 0.5; }

.send-btn {
  width: 40px;
  height: 40px;
  clip-path: polygon(8px 0, 100% 0, 100% calc(100% - 8px), calc(100% - 8px) 100%, 0 100%, 0 8px);
  border: none;
  background: rgba(255, 255, 255, 0.04);
  color: var(--val-text-dim);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.25s var(--ease-out-quart);
  flex-shrink: 0;
}

.send-btn.active {
  background: linear-gradient(135deg, #e63e4d 0%, #ff4655 100%);
  color: #fff;
  box-shadow: inset 0 0 10px rgba(255, 255, 255, 0.1), 0 3px 14px rgba(255, 70, 85, 0.35);
}

.send-btn.active:hover {
  background: linear-gradient(135deg, #d63744 0%, #ee3f4e 100%);
  transform: scale(1.06);
}

.send-btn.active:active {
  transform: scale(0.94);
}

.send-btn:disabled { cursor: not-allowed; }
.send-icon { width: 16px; height: 16px; }

.loading-spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.15);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

/* ============ MARKDOWN STYLES ============ */
.md-body :deep(strong) {
  color: #ff8a95;
  font-weight: 700;
}

.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3), .md-body :deep(h4) {
  color: var(--val-text);
  margin: 10px 0 5px;
  font-weight: 700;
}

.md-body :deep(h2) { font-size: 16px; border-bottom: 1px solid rgba(90, 110, 127, 0.1); padding-bottom: 4px; }
.md-body :deep(h3) { font-size: 14px; }

.md-body :deep(ul), .md-body :deep(ol) { padding-left: 20px; margin: 5px 0; }
.md-body :deep(li) { margin: 2px 0; }
.md-body :deep(li::marker) { color: var(--val-red); }
.md-body :deep(p) { margin: 5px 0; }

.md-body :deep(a) {
  color: var(--val-accent);
  text-decoration: none;
  border-bottom: 1px dashed rgba(0, 212, 255, 0.4);
  transition: color 0.2s, border-color 0.2s;
}

.md-body :deep(a:hover) {
  color: #5ce0ff;
  border-bottom-color: #5ce0ff;
}

.md-body :deep(code) {
  background: rgba(255, 70, 85, 0.08);
  color: #ff8a95;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 12.5px;
  font-family: 'Consolas', 'Courier New', monospace;
}

.md-body :deep(pre) {
  background: rgba(0, 0, 0, 0.25);
  border: 1px solid rgba(90, 110, 127, 0.1);
  border-radius: var(--val-radius-sm);
  padding: 12px 16px;
  overflow-x: auto;
  margin: 6px 0;
}

.md-body :deep(pre code) { background: transparent; color: var(--val-text); padding: 0; }

.md-body :deep(blockquote) {
  border-left: 2px solid rgba(255, 70, 85, 0.3);
  padding-left: 12px;
  margin: 6px 0;
  color: var(--val-text-dim);
  font-style: italic;
}

.md-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12.5px;
}

.md-body :deep(th), .md-body :deep(td) {
  border: 1px solid rgba(90, 110, 127, 0.15);
  padding: 6px 10px;
  text-align: left;
}

.md-body :deep(th) {
  background: rgba(255, 70, 85, 0.06);
  color: #ff8a95;
  font-weight: 600;
}

.md-body :deep(hr) {
  border: none;
  border-top: 1px solid rgba(90, 110, 127, 0.08);
  margin: 10px 0;
}

/* ============ TRANSITIONS ============ */
.fade-enter-active, .fade-leave-active { transition: opacity 0.35s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.msg-enter-active { transition: all 0.35s cubic-bezier(0.22, 1, 0.36, 1); }
.msg-leave-active { transition: all 0.2s ease-in; }
.msg-enter-from { opacity: 0; transform: translateY(20px); }
.msg-leave-to { opacity: 0; transform: translateX(20px); }

/* ============ RESPONSIVE ============ */
/* 【v3.28】移动端适配总纲（参考 NutUI / TDesign Mobile 规范）：
   ① 输入字号 ≥16px 防 iOS 聚焦自动放大；② 100dvh + safe-area 底部安全区；
   ③ 触控目标 ≥40px；④ 关键尺寸 clamp/vw 随屏宽缩放；⑤ 表格横向滚动 */
@media (max-width: 768px) {
  /* ---- 侧栏：抽屉式 + 遮罩 ---- */
  .kb-sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;
    z-index: 200;
    width: min(78vw, 300px);
    min-width: min(78vw, 300px);
    box-shadow: 6px 0 40px rgba(0, 0, 0, 0.5);
  }
  .kb-sidebar:not(.open) { transform: translateX(-100%); }
  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 150;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(2px);
  }
  .chat-container.sidebarActive { margin-left: 0; }

  /* ---- 顶栏：压缩腾空间 ---- */
  .chat-header { padding: 8px 12px; gap: 8px; }
  .logo-wrap { width: 30px; height: 30px; }
  .brand-text h1 { font-size: 14px; letter-spacing: 0.5px; }
  .header-tools { gap: 5px; }
  .domain-btn { padding: 4px 10px; font-size: 12px; }
  .connection-badge { display: none; }   /* 窄屏收起（连接状态侧栏底部仍有） */
  .tool-btn { min-width: 38px; min-height: 38px; padding: 6px; }
  .menu-icon { width: 20px; height: 20px; }

  /* ---- 消息区：字号上探+触控友好 ---- */
  .chat-main { padding: 12px 12px 16px; }
  .msg-row { gap: 8px; margin-bottom: 16px; }
  .msg-avatar, .avatar-img, .avatar-svg { width: 30px; height: 30px; }
  .msg-body { max-width: 86%; }
  .msg-bubble {
    font-size: 15px;   /* 手机阅读基准 */
    padding: 10px 13px;
    line-height: 1.65;
  }
  .fb-btn { padding: 6px 11px; font-size: 13px; }   /* 触控目标 ≥36px */

  /* ---- 欢迎屏：vw 随屏缩放 ---- */
  .welcome-area { padding: 20px 14px 36px; }
  .welcome-hero { margin-bottom: 22px; }
  .hero-icon { width: clamp(52px, 15vw, 72px); height: clamp(52px, 15vw, 72px); }
  .hero-title { font-size: clamp(19px, 5.5vw, 23px); }
  .hero-desc { font-size: 12.5px; }
  .quick-card { padding: 12px 14px; }

  /* ---- 输入区：防 iOS 缩放 + 安全区 ---- */
  .chat-footer { padding: 8px 10px calc(8px + env(safe-area-inset-bottom, 0px)); }
  .input-wrapper { padding: 4px; border-radius: 12px; }
  .msg-input {
    font-size: 16px;   /* ≥16px：iOS 聚焦不再自动放大页面 */
    min-height: 42px;
    max-height: 120px;
    padding: 8px 8px 8px 11px;
  }
  .send-btn { width: 44px; height: 44px; }   /* 触控目标 44px */
  .send-icon { width: 18px; height: 18px; }

  /* ---- Markdown：表格/代码块横向滚动不撑破 ---- */
  .md-body :deep(table) { display: block; overflow-x: auto; white-space: nowrap; }
  .md-body :deep(pre) { max-width: 100%; }
}

/* 超窄屏（≤480）：再收一轮 */
@media (max-width: 480px) {
  .tool-btn-text { display: none; }   /* "知识库"文字按钮收起，图标按钮保留 */
  .brand-text p { display: none; }
}
</style>

