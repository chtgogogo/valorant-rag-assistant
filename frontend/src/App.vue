<template>
  <ChatPage v-show="view === 'chat'" @manage="enterDocs" />
  <KnowledgeManage v-if="view === 'docs'" :kb-id="docsKb" @back="view = 'chat'" />
  <AdminPage v-if="view === 'admin'" @back="view = 'chat'" />
  <!-- 运营管理入口：只挂在对话页上，点击先过管理密码门（v3.30） -->
  <button v-if="view === 'chat'" class="admin-entry" @click="enterAdmin" title="工单 / 审计 运营管理">
    管理
  </button>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import ChatPage from './components/ChatPage.vue'
import KnowledgeManage from './components/KnowledgeManage.vue'
import AdminPage from './components/AdminPage.vue'
import { getAdminKey, setAdminKey } from './api.js'

const view = ref('chat')
// 知识库管理页展示的知识库：由对话页当前选中的领域带入（不再写死 valorant）
const docsKb = ref('valorant')

// 【v3.30】管理密码门：工单/知识库/审计是管理数据，不能任何人都进。
// 输入的管理密码存 localStorage（服务端每次请求都校验 X-Admin-Key，输错照样 403）。
async function enterAdmin() {
  try {
    await enterAdminGate()
  } catch {
    return
  }
  view.value = 'admin'
}

// 知识库管理同样挂管理门（上传/删除文档会改知识库，属管理操作）
async function enterDocs(kbId) {
  if (kbId) docsKb.value = kbId
  try {
    await enterAdminGate()
  } catch {
    return
  }
  view.value = 'docs'
}

async function enterAdminGate() {
  if (!getAdminKey()) {
    try {
      const r = await ElMessageBox.prompt(
        '管理功能需要管理密码（服务端 .env 的 ADMIN_PASSWORD）',
        '管理员验证',
        {
          confirmButtonText: '进入',
          cancelButtonText: '取消',
          inputType: 'password',
          inputPlaceholder: '输入管理密码',
          inputValidator: (v) => (v && v.trim() ? true : '请输入管理密码'),
        })
      setAdminKey(r.value.trim())
    } catch {
      throw new Error('取消')
    }
  }
}
</script>

<style scoped>
/* 管理入口：斜切舱门风格，hover 扫光 */
.admin-entry {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 40;
  padding: 8px 22px;
  clip-path: polygon(9px 0, 100% 0, 100% calc(100% - 9px), calc(100% - 9px) 100%, 0 100%, 0 9px);
  border: 1px solid rgba(90, 110, 127, 0.25);
  background: rgba(15, 25, 35, 0.88);
  color: var(--val-text-dim);
  font-size: 13px;
  letter-spacing: 2px;
  cursor: pointer;
  transition: all 0.25s var(--ease-out-quart);
  backdrop-filter: blur(4px);
  overflow: hidden;
}

.admin-entry::after {
  content: '';
  position: absolute;
  top: -40%;
  left: -80%;
  width: 50%;
  height: 180%;
  background: linear-gradient(105deg, transparent, rgba(255, 255, 255, 0.08), transparent);
  transform: skewX(-20deg);
  transition: left 0.55s var(--ease-out-expo);
  pointer-events: none;
}

.admin-entry:hover::after {
  left: 130%;
}

.admin-entry:hover {
  color: var(--val-red);
  border-color: rgba(255, 70, 85, 0.4);
  box-shadow: 0 4px 18px rgba(255, 70, 85, 0.15);
}

/* 【v3.28】移动端：抬高避开底部输入框 + 安全区（按钮在本组件 scoped 作用域，须在此覆盖） */
@media (max-width: 768px) {
  .admin-entry {
    bottom: calc(88px + env(safe-area-inset-bottom, 0px));
    right: 12px;
    padding: 10px 16px;
    font-size: 14px;
  }
}
</style>
