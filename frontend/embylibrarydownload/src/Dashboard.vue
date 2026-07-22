<script setup>
defineProps({ config: { type: Object, default: () => ({}) } })
</script>
<template>
  <VCard variant="outlined" class="h-100">
    <VCardText>
      <div class="d-flex align-center ga-3 mb-4"><VIcon icon="mdi-movie-filter" color="primary" size="32" /><div><div class="text-subtitle-1 font-weight-bold">EMBY 库筛选下载</div><div class="text-body-2 text-medium-emphasis">从版本库存出发，最多保留三个受控版本。</div></div></div>
      <div class="d-flex ga-6"><div><strong class="text-h5">{{ config.stats?.inventory_versions || 0 }}</strong><div class="text-caption">版本记录</div></div><div><strong class="text-h5">{{ config.stats?.active_targets || 0 }}</strong><div class="text-caption">启用目标</div></div><div><strong class="text-h5">{{ config.stats?.active_jobs || 0 }}</strong><div class="text-caption">队列占位</div></div></div>
    </VCardText>
  </VCard>
</template>
