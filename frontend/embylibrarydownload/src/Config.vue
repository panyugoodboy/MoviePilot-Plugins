<script setup>
import { ref } from 'vue'

const props = defineProps({ initialConfig: { type: Object, default: () => ({}) } })
const emit = defineEmits(['save', 'close', 'switch'])
const config = ref({ ...props.initialConfig })
</script>
<template>
  <VCard flat>
    <VCardTitle>联动 EMBY 库筛选下载</VCardTitle>
    <VCardText>
      <VAlert type="info" variant="tonal" class="mb-4">完整的站点、媒体库、质量规则和路径设置位于侧栏“EMBY库筛选下载”页面。</VAlert>
      <VSwitch v-model="config.enabled" label="启用插件" color="primary" />
      <VSwitch v-model="config.auto_download" label="允许自动下载" color="primary" />
      <VSelect v-model="config.max_versions" label="每个影片/每集最多版本" :items="[1,2,3]" />
    </VCardText>
    <VCardActions><VBtn variant="text" min-height="44" @click="emit('switch')">查看详情</VBtn><VSpacer /><VBtn variant="text" min-height="44" @click="emit('close')">取消</VBtn><VBtn color="primary" min-height="44" @click="emit('save', config)">保存</VBtn></VCardActions>
  </VCard>
</template>
