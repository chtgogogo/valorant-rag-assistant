import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// 【v3.30】管理密码（X-Admin-Key）：管理接口（工单/知识库/审计）需要。
// 密码存在 localStorage——只是"记住"，安全由服务端校验兜底。
const ADMIN_KEY_STORAGE = 'rag-admin-key'
export function getAdminKey() {
  return localStorage.getItem(ADMIN_KEY_STORAGE) || ''
}
export function setAdminKey(key) {
  localStorage.setItem(ADMIN_KEY_STORAGE, key)
}
export function clearAdminKey() {
  localStorage.removeItem(ADMIN_KEY_STORAGE)
}
api.interceptors.request.use((config) => {
  const key = getAdminKey()
  if (key) config.headers['X-Admin-Key'] = key
  return config
})

// 发送消息
export function sendMessage(sessionId, question, kbId = 'valorant') {
  return api.post('/chat/send', {
    session_id: sessionId,
    question: question,
    kb_id: kbId,
  })
}

// 流式发送消息（SSE）：answer 为流式回答的来源列表，onToken 逐字回调，最终 resolve 完整结果
export async function sendMessageStream(sessionId, question, kbId, { onToken, onSources } = {}) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question, kb_id: kbId }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`流式接口异常: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let finalAnswer = ''
  let finalSources = []
  let receivedDone = false // v3.10：是否收到 done 事件（流结束却没收到 = SSE 连接中断）

  // 解析 SSE：按空行分隔事件，"event: xxx" + "data: {...}"
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() // 最后一段可能不完整，留到下一轮
    for (const chunk of chunks) {
      const eventLine = chunk.split('\n').find((l) => l.startsWith('event:'))
      const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      let payload
      try { payload = JSON.parse(dataLine.slice(5).trim()) } catch { continue }
      const type = eventLine ? eventLine.slice(6).trim() : ''
      if (type === 'sources') {
        finalSources = payload.sources || []
        if (onSources) onSources(finalSources)
      } else if (type === 'token') {
        finalAnswer += payload.delta || ''
        if (onToken) onToken(payload.delta || '')
      } else if (type === 'error') {
        // v3.10：后端 LLM 生成失败的结构化错误事件（限流/5xx/超时降级）→
        // 抛出带标记的错误让调用方渲染错误气泡并停止 loading；
        // 不再走普通接口降级重问，避免二次撞限流继续转圈
        const err = new Error(payload.message || '模型服务繁忙，请稍后再试')
        err.isLlmError = true
        err.llmMessage = payload.message || '模型服务繁忙，请稍后再试'
        try { await reader.cancel() } catch { /* 流已自行结束 */ }
        throw err
      } else if (type === 'done') {
        receivedDone = true
        finalAnswer = payload.answer || finalAnswer
      }
    }
  }
  if (!receivedDone) {
    // v3.10：SSE 流提前断开（没收到 done）→ 抛错提示，避免空泡/静默失败后界面卡住
    const err = new Error('网络连接中断，请稍后再试')
    err.isStreamInterrupted = true
    err.llmMessage = '网络连接中断，请稍后再试'
    throw err
  }
  return { answer: finalAnswer, sources: finalSources }
}

// 【W8-卡5】Agent 流式问答（SSE 轨迹版）：onEvent 逐事件回调（tool_call/tool_result/
// final_answer/degraded，未知类型也原样上抛由调用方兜底——加新事件前端零改动），
// done 时 resolve 完整结果；error 事件抛带 isLlmError 标记的错误（与 Workflow 流式同款约定）
export async function sendAgentChatStream(sessionId, question, kbId, { onEvent } = {}) {
  const resp = await fetch('/api/agent/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question, kb_id: kbId }),
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`Agent 流式接口异常: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let final = { answer: '', sources: [], degraded: false }
  let receivedDone = false

  // 解析 SSE：按空行分隔事件（与 sendMessageStream 同款解析）
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop()
    for (const chunk of chunks) {
      const eventLine = chunk.split('\n').find((l) => l.startsWith('event:'))
      const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'))
      if (!dataLine) continue
      let payload
      try { payload = JSON.parse(dataLine.slice(5).trim()) } catch { continue }
      const type = eventLine ? eventLine.slice(6).trim() : ''
      if (type === 'done') {
        receivedDone = true
        final = {
          answer: payload.answer || final.answer,
          sources: payload.sources || [],
          degraded: !!payload.degraded,
        }
      } else if (type === 'error') {
        const err = new Error(payload.message || 'Agent 执行失败，请稍后再试')
        err.isLlmError = true
        err.llmMessage = payload.message || 'Agent 执行失败，请稍后再试'
        try { await reader.cancel() } catch { /* 流已自行结束 */ }
        throw err
      } else if (onEvent) {
        onEvent(payload) // tool_call / tool_result / final_answer / degraded / 未来新类型
      }
    }
  }
  if (!receivedDone) {
    const err = new Error('网络连接中断，请稍后再试')
    err.isStreamInterrupted = true
    err.llmMessage = '网络连接中断，请稍后再试'
    throw err
  }
  return final
}

// 清空历史
export function clearHistory(sessionId) {
  return api.post('/chat/clear', null, {
    params: { session_id: sessionId },
  })
}

// 回滚历史
export function rollbackHistory(sessionId, turnIndex) {
  return api.post('/chat/rollback', null, {
    params: { session_id: sessionId, turn_index: turnIndex },
  })
}

// 按会话ID读取历史消息（页面刷新后恢复对话）
export function getHistory(sessionId) {
  return api.get('/chat/history', {
    params: { session_id: sessionId },
  })
}

// 模块测试
export function testChat() {
  return api.get('/chat/test')
}

// 提交评价：对一条 AI 回复点赞/点踩（同问题同答案重复评价时后端覆盖原记录）
// 【W8-卡6】context = 最近 ≤3 轮完整对话（含本轮），meta = 领域/回答路径/trace_id，供坏例归因分析；旧调用不传兼容
export function sendFeedback(sessionId, question, answer, rating, context = null, meta = null) {
  return api.post('/feedback', {
    session_id: sessionId,
    question: question,
    answer: answer,
    rating: rating,
    context: context,
    meta: meta,
  })
}

// 可用领域列表（前端一键切换知识库）
export function getDomains() {
  return api.get('/domain/list')
}

// 知识库文档管理
export function listDocuments(kbId = 'valorant') {
  return api.get('/document/list', {
    params: { kb_id: kbId },
  })
}

export function uploadDocument(file, kbId = 'valorant') {
  const formData = new FormData()
  formData.append('file', file)
  return api.post('/document/upload', formData, {
    params: { kb_id: kbId },
    timeout: 180000,
  })
}

export function deleteDocument(docId, kbId = 'valorant') {
  return api.delete('/document/delete', {
    params: { doc_id: docId, kb_id: kbId },
  })
}

// 向量库统计
export function getVectorStats(kbId = 'valorant') {
  return api.get('/vector/stats', {
    params: { kb_id: kbId },
  })
}

// ---- 运营管理页（工单 / 审计 / 反馈）----

// 工单列表（status 可选：open/resolved/closed，不传查全部）
export function getTickets(status = '', limit = 100) {
  return api.get('/ticket/list', {
    params: { status: status || undefined, limit },
  })
}

// 工单闭环统计（各状态数量 + 回流数）
export function getTicketStats() {
  return api.get('/ticket/stats')
}

// 处理工单：填标准答案；feedback 为 true 时回流知识库
export function resolveTicket(ticketId, answer, feedback = true) {
  return api.post(`/ticket/${ticketId}/resolve`, {
    answer: answer,
    feedback: feedback,
  })
}

// 关闭工单（不填答案、不回流）
export function closeTicket(ticketId) {
  return api.post(`/ticket/${ticketId}/close`)
}

// 删除工单记录（已关闭工单的清理；不影响已回流的知识库文档）
export function deleteTicket(ticketId) {
  return api.delete(`/ticket/${ticketId}`)
}

// 最近 N 天问答审计记录
export function getRecentAudit(days = 7) {
  return api.get('/audit/recent', {
    params: { days: days },
  })
}

// 最近 N 条用户反馈（点赞/点踩，管理页只读展示）
export function getRecentFeedback(limit = 10) {
  return api.get('/feedback/recent', {
    params: { limit: limit },
  })
}

export default api
