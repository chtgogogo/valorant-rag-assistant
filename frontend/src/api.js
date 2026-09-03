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

export default api
