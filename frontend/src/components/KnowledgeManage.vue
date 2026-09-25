<template>
  <div class="kb-page">
    <header class="kb-header">
      <button class="back-btn" @click="$emit('back')" title="返回智能问答">
        <svg viewBox="0 0 24 24" width="18" height="18">
          <path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>
      <div>
        <h1>知识库管理</h1>
        <p>管理「{{ kbId }}」知识库文档，上传后自动解析、切分、向量化并用于回答</p>
      </div>
      <div class="header-actions">
        <button class="refresh-btn" @click="loadAll" :disabled="loading">
          <span v-if="loading" class="spinner"></span>
          <span v-else>刷新</span>
        </button>
      </div>
    </header>

    <main class="kb-main">
      <section class="stats-row">
        <div class="stat-card">
          <div class="stat-value">{{ stats.chunk_count ?? 0 }}</div>
          <div class="stat-label">向量块数量</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ docs.length }}</div>
          <div class="stat-label">文档数量</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ kbId }}</div>
          <div class="stat-label">当前知识库</div>
        </div>
      </section>

      <section class="upload-card">
        <div class="card-title">上传新文档</div>
        <div class="upload-row">
          <label class="file-label">
            <input
              type="file"
              accept=".md,.txt,.docx,.pdf"
              :disabled="uploading"
              @change="onFileChange"
            />
            <span class="file-name">{{ selectedFile ? selectedFile.name : '选择 .md / .txt / .docx / .pdf 文件' }}</span>
          </label>
          <button class="upload-btn" @click="handleUpload" :disabled="!selectedFile || uploading">
            {{ uploading ? '上传中...' : '上传并索引' }}
          </button>
        </div>
        <p class="upload-tip">上传后系统会解析文档、拆分文本块、生成向量并加入 RAG 知识库。</p>
      </section>

      <section class="doc-card">
        <div class="card-title-row">
          <div class="card-title">文档列表</div>
          <span class="doc-total">共 {{ docs.length }} 份</span>
        </div>
        <div v-if="docs.length === 0" class="empty-tip">暂无文档，请先上传。</div>
        <div v-else class="doc-table">
          <div class="doc-row doc-head">
            <span>文档名称</span>
            <span>上传时间</span>
            <span>块数</span>
            <span>操作</span>
          </div>
          <div v-for="doc in docs" :key="doc.doc_id" class="doc-row">
            <span class="doc-name" :title="doc.doc_name">{{ doc.doc_name }}</span>
            <span class="doc-time">{{ doc.upload_time }}</span>
            <span class="doc-chunks">{{ doc.chunk_count }}</span>
            <span>
              <button class="delete-btn" @click="handleDelete(doc)" :disabled="deleting === doc.doc_id">
                {{ deleting === doc.doc_id ? '删除中' : '删除' }}
              </button>
            </span>
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
  listDocuments,
  uploadDocument,
  deleteDocument,
  getVectorStats,
} from '../api.js'

// 当前管理的知识库由对话页选中的领域带入（App.vue 传入），不再写死 valorant
const props = defineProps({
  kbId: { type: String, default: 'valorant' },
})
const emit = defineEmits(['back'])
const docs = ref([])
const stats = ref({})
const selectedFile = ref(null)
const uploading = ref(false)
const loading = ref(false)
const deleting = ref('')

async function loadAll() {
  loading.value = true
  try {
    const [docRes, statRes] = await Promise.all([
      listDocuments(props.kbId),
      getVectorStats(props.kbId),
    ])
    if (docRes.data && docRes.data.code === 200) {
      docs.value = docRes.data.data || []
    } else {
      ElMessage.error(docRes.data?.msg || '读取文档列表失败')
    }
    if (statRes.data && statRes.data.code === 200) {
      stats.value = statRes.data.data || {}
    }
  } catch (e) {
    ElMessage.error('加载知识库失败，请检查后端服务')
  } finally {
    loading.value = false
  }
}

function onFileChange(e) {
  selectedFile.value = e.target.files[0] || null
}

async function handleUpload() {
  if (!selectedFile.value) return
  uploading.value = true
  try {
    const res = await uploadDocument(selectedFile.value, props.kbId)
    if (res.data && res.data.code === 200) {
      ElMessage.success(`上传成功，已生成 ${res.data.data?.chunk_count || 0} 个文本块`)
      selectedFile.value = null
      const fileInput = document.querySelector('.file-label input[type=file]')
      if (fileInput) fileInput.value = ''
      await loadAll()
    } else {
      ElMessage.error(res.data?.msg || '上传失败')
    }
  } catch (e) {
    ElMessage.error('上传失败，请检查网络或后端服务')
  } finally {
    uploading.value = false
  }
}

async function handleDelete(doc) {
  try {
    await ElMessageBox.confirm(`确定删除「${doc.doc_name}」吗？对应向量数据也会一并删除。`, '删除确认', {
      type: 'warning',
    })
  } catch {
    return
  }
  deleting.value = doc.doc_id
  try {
    const res = await deleteDocument(doc.doc_id, props.kbId)
    if (res.data && res.data.code === 200) {
      ElMessage.success('文档已删除')
      await loadAll()
    } else {
      ElMessage.error(res.data?.msg || '删除失败')
    }
  } catch (e) {
    ElMessage.error('删除失败，请检查网络或后端服务')
  } finally {
    deleting.value = ''
  }
}

onMounted(loadAll)
</script>

<style scoped>
.kb-page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--val-bg);
  color: var(--val-text);
  overflow: hidden;
}

.kb-header {
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

.kb-header h1 {
  font-size: 18px;
  font-weight: 700;
}

.kb-header p {
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

.kb-main {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
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
  color: var(--val-red);
}

.stat-label {
  font-size: 12px;
  color: var(--val-text-dim);
  margin-top: 4px;
}

.upload-card,
.doc-card {
  background: rgba(30, 45, 58, 0.65);
  border: 1px solid var(--val-border);
  border-radius: 12px;
  padding: 20px;
}

.card-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 14px;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.doc-total {
  font-size: 12px;
  color: var(--val-text-dim);
}

.upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.file-label {
  flex: 1;
  min-width: 240px;
  display: block;
  border: 1px dashed var(--val-border);
  border-radius: 8px;
  padding: 12px 16px;
  cursor: pointer;
  transition: all 0.2s;
  background: rgba(15, 25, 35, 0.4);
}

.file-label:hover {
  border-color: var(--val-red);
  background: rgba(255, 70, 85, 0.05);
}

.file-label input {
  display: none;
}

.file-name {
  font-size: 13px;
  color: var(--val-text);
  word-break: break-all;
}

.upload-btn {
  padding: 12px 22px;
  border-radius: 8px;
  border: none;
  background: var(--val-red);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-btn:hover:not(:disabled) {
  background: var(--val-red-dark);
  transform: translateY(-1px);
}

.upload-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.upload-tip {
  font-size: 12px;
  color: var(--val-text-dim);
  margin-top: 10px;
}

.empty-tip {
  text-align: center;
  color: var(--val-text-dim);
  padding: 40px 0;
  font-size: 13px;
}

.doc-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.doc-row {
  display: grid;
  grid-template-columns: 2fr 1.2fr 0.6fr 0.8fr;
  gap: 12px;
  align-items: center;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  background: rgba(15, 25, 35, 0.35);
}

.doc-row.doc-head {
  background: rgba(255, 255, 255, 0.03);
  color: var(--val-text-dim);
  font-size: 12px;
  font-weight: 600;
}

.doc-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-time {
  color: var(--val-text-dim);
}

.doc-chunks {
  text-align: center;
  color: var(--val-accent);
}

.delete-btn {
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid rgba(255, 70, 85, 0.3);
  background: rgba(255, 70, 85, 0.08);
  color: #ff8a95;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}

.delete-btn:hover:not(:disabled) {
  background: rgba(255, 70, 85, 0.18);
}

.delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
    grid-template-columns: 1fr;
  }
  .doc-row {
    grid-template-columns: 1.5fr 0.8fr 0.5fr 0.6fr;
    font-size: 12px;
  }
}
</style>
