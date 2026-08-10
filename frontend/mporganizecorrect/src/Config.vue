<script setup>
import { ref } from 'vue'

const props = defineProps({ initialConfig: { type: Object, default: () => ({}) } })
const emit = defineEmits(['save', 'close', 'switch'])
const config = ref({
  enabled: false,
  scan_cron: '0 4 * * *',
  auto_correct: false,
  auto_batch_limit: 5,
  cleanup_old_after_correct: true,
  notify_enabled: true,
  ...props.initialConfig,
})
</script>

<template>
  <VCard flat>
    <VCardTitle>MP整理纠正</VCardTitle>
    <VCardText>
      <VAlert type="info" variant="tonal" class="mb-4">
        完整扫描、人工选片、路径预览和删除确认位于侧栏“MP整理纠正”页面。
      </VAlert>
      <VSwitch v-model="config.enabled" label="启用插件" color="primary" />
      <VTextField v-model="config.scan_cron" label="扫描 Cron" hint="五段 Cron 表达式" persistent-hint />
      <VSwitch v-model="config.auto_correct" label="允许定时自动纠正唯一精确匹配的电影" color="primary" />
      <VSwitch v-model="config.cleanup_old_after_correct" label="新整理验证成功后清理旧英文媒体" color="primary" />
    </VCardText>
    <VCardActions>
      <VBtn variant="text" min-height="44" @click="emit('switch')">查看详情</VBtn>
      <VSpacer />
      <VBtn variant="text" min-height="44" @click="emit('close')">取消</VBtn>
      <VBtn color="primary" min-height="44" @click="emit('save', config)">保存</VBtn>
    </VCardActions>
  </VCard>
</template>
