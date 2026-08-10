<script setup>
defineProps({ config: { type: Object, default: () => ({}) } })
</script>

<template>
  <VCard variant="outlined" class="h-100">
    <VCardText>
      <div class="d-flex align-center ga-3 mb-4">
        <VIcon icon="mdi-folder-refresh-outline" color="primary" size="32" />
        <div><div class="text-subtitle-1 font-weight-bold">MP整理纠正</div><div class="text-body-2 text-medium-emphasis">源文件永久保留，纠正成功后再清理旧媒体。</div></div>
      </div>
      <div class="d-flex flex-wrap ga-6">
        <div><strong class="text-h5">{{ config.stats?.ready || 0 }}</strong><div class="text-caption">可批量</div></div>
        <div><strong class="text-h5">{{ config.stats?.manual || 0 }}</strong><div class="text-caption">待人工</div></div>
        <div><strong class="text-h5">{{ config.stats?.cleanup_pending || 0 }}</strong><div class="text-caption">待清理</div></div>
      </div>
    </VCardText>
  </VCard>
</template>
