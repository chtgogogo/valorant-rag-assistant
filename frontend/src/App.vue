<template>
  <ChatPage v-show="view === 'chat'" @manage="view = 'docs'" />
  <KnowledgeManage v-if="view === 'docs'" @back="view = 'chat'" />
  <AdminPage v-if="view === 'admin'" @back="view = 'chat'" />
  <!-- 运营管理入口：只挂在对话页上，点一下切到管理页（组件切换方式同知识库管理） -->
  <button v-if="view === 'chat'" class="admin-entry" @click="view = 'admin'" title="工单 / 审计 运营管理">
    管理
  </button>
</template>

<script setup>
import { ref } from 'vue'
import ChatPage from './components/ChatPage.vue'
import KnowledgeManage from './components/KnowledgeManage.vue'
import AdminPage from './components/AdminPage.vue'

const view = ref('chat')
</script>

<style scoped>
.admin-entry {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 40;
  padding: 8px 18px;
  border-radius: 999px;
  border: 1px solid rgba(90, 110, 127, 0.25);
  background: rgba(15, 25, 35, 0.85);
  color: var(--val-text-dim);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  backdrop-filter: blur(4px);
}

.admin-entry:hover {
  color: var(--val-red);
  border-color: rgba(255, 70, 85, 0.4);
}
</style>
