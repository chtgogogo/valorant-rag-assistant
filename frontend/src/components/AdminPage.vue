<template>
  <div class="admin-page">
    <header class="admin-header">
      <button class="back-btn" @click="$emit('back')" title="返回智能问答">
        <svg viewBox="0 0 24 24" width="18" height="18">
          <path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <div>
        <h1>运营管理</h1>
        <p>工单处理 / 闭环统计 / 问答审计 · 本地运营演示用，鉴权由后续用户体系承担</p>
      </div>
      <div class="header-actions">
        <button class="refresh-btn" @click="loadAll" :disabled="loading">
          <span v-if="loading" class="spinner"></span>
          <span v-else>刷新</span>
        </button>
      </div>
    </header>

    <main class="admin-main">
      <!-- 区块一：工单闭环统计卡 -->
      <section class="stats-row">
        <div class="stat-card">
          <div class="stat-value warn">{{ stats.open ?? 0 }}</div>
          <div class="stat-label">待处理工单</div>
        </div>
        <div class="stat-card">
          <div class="stat-value ok">{{ stats.resolved ?? 0 }}</div>
          <div class="stat-label">已解决</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.closed ?? 0 }}</div>
          <div class="stat-label">已关闭</div>
        </div>
        <div class="stat-card">
          <div class="stat-value accent">{{ stats.fed_back ?? 0 }}</div>
          <div class="stat-label">已回流知识库</div>
        </div>
      </section>

      <!-- 区块二：工单列表（可筛选 / 解决 / 关闭） -->
      <section class="panel-card">
        <div class="card-title-row">
          <div class="card-title">工单列表</div>
          <div class="filter-row">
            <button
              v-for="opt in statusOptions"
              :key="opt.value"
              class="filter-btn"
              :class="{ active: filterStatus === opt.value }"
              @click="switchFilter(opt.value)"
            >{{ opt.label }}</button>
          </div>
        </div>
        <div v-if="tickets.length === 0" class="empty-tip">当前筛选下暂无工单。</div>
        <div v-else class="data-table">
          <div class="table-row table-head">
            <span>状态</span>
            <span>问题摘要</span>
            <span>创建时间</span>
            <span>操作</span>
          </div>
          <div v-for="t in tickets" :key="t.id" class="table-row">
            <span>
              <span class="status-tag" :class="t.status">{{ statusText(t.status) }}</span>
            </span>
            <span class="cell-ellipsis" :title="t.question">{{ t.question }}</span>
            <span class="cell-dim">{{ t.created_at }}</span>
            <span>
              <template v-if="t.status === 'open'">
                <button class="action-btn resolve" @click="handleResolve(t)" :disabled="acting === t.id">
                  {{ acting === t.id ? '处理中' : '解决' }}
                </button>
                <button class="action-btn close" @click="handleClose(t)" :disabled="acting === t.id">关闭</button>
              </template>
              <span v-else class="cell-dim">{{ t.fed_back ? '已回流' : '—' }}</span>
            </span>
          </div>
        </div>
      </section>

      <!-- 区块三：最近问答审计 -->
      <section class="panel-card">
        <div class="card-title-row">
          <div class="card-title">最近问答审计（7 天内）</div>
          <span class="card-sub">共 {{ audit.length }} 条</span>
        </div>
        <div v-if="audit.length === 0" class="empty-tip">暂无审计记录。</div>
        <div v-else class="data-table">
          <div class="table-row audit-head audit-grid">
            <span>时间</span>
            <span>用户问题</span>
            <span>检索管线</span>
            <span>来源数</span>
            <span>耗时</span>
          </div>
          <div v-for="(a, i) in audit.slice(0, 50)" :key="i" class="table-row audit-grid">
            <span class="cell-dim">{{ a.time }}</span>
            <span class="cell-ellipsis" :title="a.question">{{ a.question }}</span>
            <span class="cell-dim">{{ a.pipeline || '—' }}</span>
            <span class="cell-accent">{{ (a.sources || []).length }}</span>
            <span class="cell-dim">{{ a.latency_ms ?? '—' }} ms</span>
          </div>
        </div>
      </section>

      <!-- 附加小区块：最近用户反馈（只读，不喧宾夺主） -->
      <section class="panel-card compact">
        <div class="card-title-row">
          <div class="card-title">最近用户反馈</div>
          <span class="card-sub">共 {{ feedbacks.length }} 条</span>
        </div>
        <div v-if="feedbacks.length === 0" class="empty-tip">暂无用户反馈。</div>
        <div v-else class="data-table">
          <div class="table-row fb-grid">
            <span>评价</span>
            <span>问题摘要</span>
            <span>时间</span>
          </div>
          <div v-for="f in feedbacks" :key="f.id" class="table-row fb-grid">
            <span><span class="status-tag" :class="f.rating === 'up' ? 'resolved' : 'open'">{{ f.rating === 'up' ? '赞' : '踩' }}</span></span>
            <span class="cell-ellipsis" :title="f.question">{{ f.question }}</span>
            <span class="cell-dim">{{ f.created_at }}</span>
          </div>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getTickets,
  getTicketStats,
  resolveTicket,
  closeTicket,
  getRecentAudit,
  getRecentFeedback,
} from '../api.js'

const emit = defineEmits(['back'])

const tickets = ref([])
const stats = ref({})
const audit = ref([])
const feedbacks = ref([])
const filterStatus = ref('')
const loading = ref(false)
const acting = ref('')

const statusOptions = [
  { label: '全部', value: '' },
  { label: '待处理', value: 'open' },
  { label: '已解决', value: 'resolved' },
  { label: '已关闭', value: 'closed' },
]

function statusText(s) {
  return { open: '待处理', resolved: '已解决', closed: '已关闭' }[s] || s
}

async function loadAll() {
  loading.value = true
  try {
    const [ticketRes, statRes, auditRes, fbRes] = await Promise.all([
      getTickets(filterStatus.value),
      getTicketStats(),
      getRecentAudit(7),
      getRecentFeedback(10),
    ])
    if (ticketRes.data && ticketRes.data.code === 200) {
      tickets.value = ticketRes.data.data?.tickets || []
    } else {
      ElMessage.error(ticketRes.data?.msg || '读取工单列表失败')
    }
    if (statRes.data && statRes.data.code === 200) {
      stats.value = statRes.data.data || {}
    }
    if (auditRes.data && auditRes.data.code === 200) {
      audit.value = auditRes.data.data || []
    }
    if (fbRes.data && fbRes.data.code === 200) {
      feedbacks.value = fbRes.data.data?.feedback || []
    }
  } catch (e) {
    ElMessage.error('加载运营数据失败，请检查后端服务')
  } finally {
    loading.value = false
  }
}

function switchFilter(v) {
  filterStatus.value = v
  loadTickets()
}

async function loadTickets() {
  try {
    const res = await getTickets(filterStatus.value)
    if (res.data && res.data.code === 200) {
      tickets.value = res.data.data?.tickets || []
    } else {
      ElMessage.error(res.data?.msg || '读取工单列表失败')
    }
  } catch (e) {
    ElMessage.error('读取工单列表失败，请检查后端服务')
  }
}

async function handleResolve(t) {
  let answer
  try {
    const r = await ElMessageBox.prompt(`为「${t.question}」填写标准答案：`, '处理工单', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputPlaceholder: '输入人工标准答案（不能为空）',
      inputValidator: (v) => (v && v.trim() ? true : '标准答案不能为空'),
    })
    answer = r.value.trim()
  } catch {
    return
  }
  let feedback = true
  try {
    await ElMessageBox.confirm('是否将该标准答案回流知识库？（回流后同类问题下次可直接命中）', '回流确认', {
      confirmButtonText: '回流',
      cancelButtonText: '不回流',
      type: 'info',
    })
  } catch {
    feedback = false
  }
  acting.value = t.id
  try {
    const res = await resolveTicket(t.id, answer, feedback)
    if (res.data && res.data.code === 200) {
      ElMessage.success(res.data.msg || '工单已处理')
      await Promise.all([loadTickets(), loadStats()])
    } else {
      ElMessage.error(res.data?.msg || '处理失败')
    }
  } catch (e) {
    ElMessage.error('处理失败，请检查网络或后端服务')
  } finally {
    acting.value = ''
  }
}

async function loadStats() {
  try {
    const res = await getTicketStats()
    if (res.data && res.data.code === 200) {
      stats.value = res.data.data || {}
    }
  } catch (e) {
    /* 统计刷新失败不打断主流程 */
  }
}

async function handleClose(t) {
  try {
    await ElMessageBox.confirm(`确定关闭工单「${t.question}」吗？关闭后不回流知识库。`, '关闭确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  acting.value = t.id
  try {
    const res = await closeTicket(t.id)
    if (res.data && res.data.code === 200) {
      ElMessage.success('工单已关闭')
      await Promise.all([loadTickets(), loadStats()])
    } else {
      ElMessage.error(res.data?.msg || '关闭失败')
    }
  } catch (e) {
    ElMessage.error('关闭失败，请检查网络或后端服务')
  } finally {
    acting.value = ''
  }
}

onMounted(loadAll)
</script>

<style scoped>
.admin-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--val-bg);
  color: var(--val-text);
  overflow: hidden;
}

.admin-header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 24px;
  border-bottom: 1px solid var(--val-border);
  background: rgba(15, 25, 35, 0.9);
  flex-shrink: 0;
  z-index: 2;
}

.back-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border: 1px solid rgba(90, 110, 127, 0.2);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
  cursor: pointer;
  transition: all 0.2s;
}

.back-btn:hover {
  color: var(--val-red);
  border-color: rgba(255, 70, 85, 0.4);
  transform: translateX(-2px);
}

.admin-header h1 {
  font-size: 18px;
  font-weight: 700;
}

.admin-header p {
  font-size: 12px;
  color: var(--val-text-dim);
  margin-top: 2px;
}

.header-actions {
  margin-left: auto;
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  border: 1px solid rgba(90, 110, 127, 0.2);
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  color: var(--val-accent);
  border-color: rgba(0, 212, 255, 0.3);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.admin-main {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

.stat-card {
  background: linear-gradient(135deg, rgba(30, 45, 58, 0.9), rgba(15, 25, 35, 0.9));
  border: 1px solid var(--val-border);
  border-radius: 12px;
  padding: 18px 20px;
}

.stat-value {
  font-size: 26px;
  font-weight: 800;
  color: var(--val-text);
}

.stat-value.warn {
  color: var(--val-red);
}

.stat-value.ok {
  color: #3dd68c;
}

.stat-value.accent {
  color: var(--val-accent);
}

.stat-label {
  font-size: 12px;
  color: var(--val-text-dim);
  margin-top: 4px;
}

.panel-card {
  background: rgba(30, 45, 58, 0.65);
  border: 1px solid var(--val-border);
  border-radius: 12px;
  padding: 20px;
}

.panel-card.compact {
  padding: 16px 20px;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 10px;
}

.card-sub {
  font-size: 12px;
  color: var(--val-text-dim);
}

.filter-row {
  display: flex;
  gap: 8px;
}

.filter-btn {
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid rgba(90, 110, 127, 0.25);
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.filter-btn:hover {
  color: var(--val-text);
  border-color: rgba(0, 212, 255, 0.3);
}

.filter-btn.active {
  color: #fff;
  background: var(--val-red);
  border-color: var(--val-red);
}

.data-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.table-row {
  display: grid;
  grid-template-columns: 0.8fr 2.4fr 1.1fr 1fr;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  background: rgba(15, 25, 35, 0.35);
}

.table-row.audit-grid {
  grid-template-columns: 1.3fr 2.4fr 1fr 0.6fr 0.7fr;
}

.table-row.fb-grid {
  grid-template-columns: 0.6fr 3fr 1.1fr;
}

.table-row.table-head {
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
  font-size: 12px;
  font-weight: 600;
}

.status-tag {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
}

.status-tag.open {
  color: #ffb3ba;
  background: rgba(255, 70, 85, 0.12);
  border: 1px solid rgba(255, 70, 85, 0.3);
}

.status-tag.resolved {
  color: #8ce8bb;
  background: rgba(61, 214, 140, 0.1);
  border: 1px solid rgba(61, 214, 140, 0.3);
}

.status-tag.closed {
  color: var(--val-text-dim);
  background: rgba(139, 155, 171, 0.08);
  border: 1px solid rgba(139, 155, 171, 0.25);
}

.cell-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-dim {
  color: var(--val-text-dim);
}

.cell-accent {
  color: var(--val-accent);
}

.action-btn {
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  margin-right: 6px;
}

.action-btn.resolve {
  border: 1px solid rgba(61, 214, 140, 0.35);
  background: rgba(61, 214, 140, 0.08);
  color: #8ce8bb;
}

.action-btn.resolve:hover:not(:disabled) {
  background: rgba(61, 214, 140, 0.18);
}

.action-btn.close {
  border: 1px solid rgba(90, 110, 127, 0.3);
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
}

.action-btn.close:hover:not(:disabled) {
  color: var(--val-text);
  border-color: rgba(139, 155, 171, 0.5);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.empty-tip {
  text-align: center;
  color: var(--val-text-dim);
  padding: 40px 0;
  font-size: 13px;
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: var(--val-text);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }
  .table-row {
    grid-template-columns: 0.8fr 1.6fr 1fr 1fr;
    font-size: 12px;
  }
  .table-row.audit-grid {
    grid-template-columns: 1.2fr 1.6fr 0.8fr 0.5fr 0.6fr;
  }
}
</style>
