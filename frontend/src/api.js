import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
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
      } else if (type === 'done') {
        finalAnswer = payload.answer || finalAnswer
      }
    }
  }
  return { answer: finalAnswer, sources: finalSources }
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

// 模块测试
export function testChat() {
  return api.get('/chat/test')
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


export default api
