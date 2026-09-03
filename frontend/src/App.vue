<template>
  <ChatPage v-show="view === 'chat'" @manage="view = 'docs'" />
  <KnowledgeManage v-if="view === 'docs'" @back="view = 'chat'" />
</template>

<script setup>
import { ref } from 'vue'
import ChatPage from './components/ChatPage.vue'
import KnowledgeManage from './components/KnowledgeManage.vue'

const view = ref('chat')
</script>
