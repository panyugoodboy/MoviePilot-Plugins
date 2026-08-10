<script setup>
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  pluginId: { type: String, default: 'MPOrganizeCorrect' },
})

const toast = inject('moviepilot:toast', null)
const endpoint = path => `plugin/${props.pluginId}${path}`
const tab = ref('overview')
const loading = ref(false)
const actionError = ref('')
const selected = ref([])
const bootstrap = reactive({
  config: {
    enabled: false,
    scan_cron: '0 4 * * *',
    auto_correct: false,
    auto_batch_limit: 5,
    cleanup_old_after_correct: true,
    notify_enabled: true,
  },
  stats: {}, tasks: {}, cron_preview: {}, last_scan_at: '',
})
const records = reactive({ items: [], total: 0, page: 1, state: '', keyword: '', media_type: '' })
const audits = reactive({ items: [], total: 0, page: 1 })
const manualDialog = ref(false)
const batchDialog = ref(false)
const deleteDialog = ref(false)
const manualLoading = ref(false)
const batchLoading = ref(false)
const batchPreviews = ref([])
const manual = reactive({
  record: null,
  title: '',
  year: null,
  media_type: '电影',
  candidates: [],
  candidate: null,
  preview: null,
  cleanup_old: true,
})
const deletion = reactive({ delete_media: false, delete_history: false, source_safe_confirmed: false })
let pollTimer = null

const stateItems = [
  { title: '全部待处理', value: '' },
  { title: '可批量纠正', value: 'ready' },
  { title: '需人工确认', value: 'manual' },
  { title: '源文件不存在', value: 'missing_source' },
  { title: '处理失败', value: 'failed' },
  { title: '待清理旧媒体', value: 'cleanup_pending' },
  { title: '已纠正', value: 'corrected' },
  { title: '已忽略', value: 'ignored' },
]
const recordHeaders = [
  { title: '当前整理', key: 'old_title', minWidth: 190, sortable: false },
  { title: '源文件识别', key: 'query_title', minWidth: 190, sortable: false },
  { title: '中文候选', key: 'candidate', minWidth: 230, sortable: false },
  { title: '状态', key: 'state', width: 130, sortable: false },
  { title: '操作', key: 'actions', width: 190, sortable: false },
]
const auditHeaders = [
  { title: '时间', key: 'created_at', width: 180 },
  { title: '操作', key: 'action', width: 100 },
  { title: '旧标题', key: 'old_title', minWidth: 150 },
  { title: '新标题', key: 'new_title', minWidth: 150 },
  { title: '结果', key: 'status', width: 110 },
  { title: '说明', key: 'message', minWidth: 260, sortable: false },
]
const hasRunningTask = computed(() => Object.values(bootstrap.tasks || {}).some(item => item.status === 'running'))
const runningTask = computed(() => Object.values(bootstrap.tasks || {}).find(item => item.status === 'running') || null)
const selectedRecords = computed(() => records.items.filter(item => selected.value.includes(item.history_id)))
const batchableRecords = computed(() => selectedRecords.value.filter(item => item.state === 'ready' && item.candidate?.media_id))
const canBatch = computed(() => selectedRecords.value.length > 0 && selectedRecords.value.length <= 10 && batchableRecords.value.length === selectedRecords.value.length)

async function call(method, path, payload, params) {
  try {
    const options = params ? { params } : undefined
    const response = method === 'get'
      ? await props.api.get(endpoint(path), options)
      : await props.api[method](endpoint(path), payload || {})
    if (response?.success === false) throw new Error(response.message || '操作失败')
    return response?.data
  } catch (error) {
    const message = error?.response?.data?.message || error?.message || '请求失败'
    actionError.value = message
    toast?.error?.(message)
    throw error
  }
}

async function loadBootstrap(showLoading = true) {
  if (showLoading) loading.value = true
  try { Object.assign(bootstrap, await call('get', '/bootstrap') || {}) }
  finally { if (showLoading) loading.value = false }
}

async function loadOverview() {
  const data = await call('get', '/overview')
  bootstrap.stats = data?.stats || {}
  bootstrap.tasks = data?.tasks || {}
  bootstrap.last_scan_at = data?.last_scan_at || ''
}

async function loadRecords() {
  loading.value = true
  selected.value = []
  try {
    const data = await call('get', '/records', null, {
      page: records.page,
      page_size: 50,
      state: records.state,
      keyword: records.keyword,
      media_type: records.media_type,
    })
    records.items = data?.items || []
    records.total = data?.total || 0
  } finally { loading.value = false }
}

async function loadAudits() {
  const data = await call('get', '/audits', null, { page: audits.page, page_size: 50 })
  audits.items = data?.items || []
  audits.total = data?.total || 0
}

async function refreshCurrentTab() {
  if (tab.value === 'records') return loadRecords()
  if (tab.value === 'audits') return loadAudits()
  return loadOverview()
}

async function startTask(path, payload, message) {
  await call('post', path, payload)
  toast?.info?.(message)
  await loadOverview()
  startPolling()
}

function startPolling() {
  if (pollTimer) return
  pollTimer = window.setInterval(async () => {
    try {
      await loadOverview()
      if (!hasRunningTask.value) {
        window.clearInterval(pollTimer)
        pollTimer = null
        await loadBootstrap(false)
        await refreshCurrentTab()
      }
    } catch (_) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }, 1000)
}

async function scan(full = false) {
  await startTask('/scan', { full }, full ? '已开始全量扫描' : '已开始增量扫描')
}

async function openBatch() {
  if (!canBatch.value) return
  batchLoading.value = true
  batchPreviews.value = []
  try {
    for (const item of batchableRecords.value) {
      const preview = await call('post', `/records/${item.history_id}/preview`, { candidate: item.candidate })
      batchPreviews.value.push(preview)
    }
    manual.cleanup_old = bootstrap.config.cleanup_old_after_correct !== false
    batchDialog.value = true
  } finally { batchLoading.value = false }
}

async function submitBatch() {
  const items = batchableRecords.value.map(item => ({ history_id: item.history_id, candidate: item.candidate }))
  batchDialog.value = false
  await startTask('/records/correct', { items, cleanup_old: manual.cleanup_old }, `已开始纠正 ${items.length} 条记录`)
}

function openManual(record) {
  Object.assign(manual, {
    record,
    title: record.query_title || '',
    year: record.query_year || record.old_year || null,
    media_type: record.media_type || '电影',
    candidates: record.options || [],
    candidate: record.candidate?.media_id ? record.candidate : null,
    preview: null,
    cleanup_old: bootstrap.config.cleanup_old_after_correct !== false,
  })
  manualDialog.value = true
}

async function searchManual() {
  manualLoading.value = true
  manual.preview = null
  manual.candidate = null
  try {
    manual.candidates = await call('post', `/records/${manual.record.history_id}/search`, {
      title: manual.title,
      year: manual.year,
      media_type: manual.media_type,
    }) || []
  } finally { manualLoading.value = false }
}

function chooseCandidate(candidate) {
  manual.candidate = candidate
  manual.preview = null
}

async function previewManual() {
  if (!manual.candidate) return
  manualLoading.value = true
  try {
    manual.preview = await call('post', `/records/${manual.record.history_id}/preview`, {
      candidate: manual.candidate,
    })
  } finally { manualLoading.value = false }
}

async function correctManual() {
  if (!manual.preview || !manual.candidate) return
  const item = { history_id: manual.record.history_id, candidate: manual.candidate }
  manualDialog.value = false
  await startTask('/records/correct', { items: [item], cleanup_old: manual.cleanup_old }, '已开始手动纠正')
}

async function setIgnored(ignored) {
  const ids = [...selected.value]
  if (!ids.length) return
  await call('post', '/records/ignore', { history_ids: ids, ignored })
  toast?.success?.(ignored ? `已忽略 ${ids.length} 条记录` : `已恢复 ${ids.length} 条记录`)
  await loadRecords()
  await loadOverview()
}

async function retryCleanup(record) {
  await startTask('/records/cleanup', { history_ids: [record.history_id] }, '已开始重试清理旧媒体')
}

function openDelete() {
  Object.assign(deletion, { delete_media: false, delete_history: false, source_safe_confirmed: false })
  deleteDialog.value = true
}

async function submitDelete() {
  const ids = [...selected.value]
  deleteDialog.value = false
  await startTask('/records/delete', {
    history_ids: ids,
    delete_media: deletion.delete_media,
    delete_history: deletion.delete_history,
    source_safe_confirmed: deletion.source_safe_confirmed,
  }, `已开始处理 ${ids.length} 条删除请求`)
}

async function saveSettings() {
  loading.value = true
  try {
    const response = await props.api.put(`plugin/${props.pluginId}`, bootstrap.config)
    if (response?.success === false) throw new Error(response.message || '保存失败')
    toast?.success?.('设置已保存并重新加载插件')
    await loadBootstrap(false)
  } catch (error) {
    toast?.error?.(error?.response?.data?.message || error?.message || '保存失败')
  } finally { loading.value = false }
}

async function testNotification() {
  await call('post', '/notifications/test', {})
  toast?.success?.('测试通知已发送')
}

function stateMeta(state, ignored = false) {
  if (ignored) return { text: '已忽略', color: 'default', icon: 'mdi-eye-off-outline' }
  return {
    ready: { text: '可批量纠正', color: 'success', icon: 'mdi-check-decagram-outline' },
    manual: { text: '需人工确认', color: 'warning', icon: 'mdi-account-search-outline' },
    missing_source: { text: '源文件不存在', color: 'error', icon: 'mdi-file-alert-outline' },
    failed: { text: '处理失败', color: 'error', icon: 'mdi-alert-circle-outline' },
    cleanup_pending: { text: '待清理旧媒体', color: 'warning', icon: 'mdi-broom' },
    corrected: { text: '已纠正', color: 'primary', icon: 'mdi-folder-check-outline' },
    deleted: { text: '已删除', color: 'default', icon: 'mdi-delete-check-outline' },
  }[state] || { text: state || '未知', color: 'default', icon: 'mdi-help-circle-outline' }
}

function taskPercent(task) {
  const current = Number(task?.progress?.current || 0)
  const total = Number(task?.progress?.total || 0)
  return total ? Math.round(current * 100 / total) : 0
}

function auditStatus(value) {
  if (value === 'success') return { text: '成功', color: 'success' }
  if (value === 'cleanup_pending') return { text: '待清理', color: 'warning' }
  return { text: '失败', color: 'error' }
}

onMounted(async () => {
  await Promise.all([loadBootstrap(), loadRecords()])
  if (hasRunningTask.value) startPolling()
})
watch(tab, value => {
  if (value === 'records') loadRecords()
  if (value === 'audits') loadAudits()
})
onBeforeUnmount(() => { if (pollTimer) window.clearInterval(pollTimer) })
</script>

<template>
  <main class="correct-app pa-4" aria-labelledby="page-title">
    <header class="page-header">
      <div class="page-brand"><div class="brand-icon"><VIcon icon="mdi-folder-refresh-outline" size="28" /></div><div><span class="eyebrow">MOVIEPILOT ORGANIZE SAFETY</span><h1 id="page-title">MP整理纠正</h1><p>按源文件中文片名和年份纠正英文整理结果，源文件永久保留。</p></div></div>
      <VBtn class="action-btn" color="primary" prepend-icon="mdi-radar" :loading="hasRunningTask" @click="scan(false)">立即扫描</VBtn>
    </header>

    <VAlert v-if="actionError" type="error" variant="tonal" closable class="mb-4" @click:close="actionError=''">{{ actionError }}</VAlert>
    <VCard v-if="runningTask" variant="outlined" class="task-card mb-4" aria-live="polite">
      <VCardText><div class="task-heading"><div><strong>{{ runningTask.message || '任务运行中' }}</strong><small>{{ runningTask.progress?.current || 0 }} / {{ runningTask.progress?.total || '—' }}</small></div><VChip color="primary" variant="tonal">运行中</VChip></div><VProgressLinear :model-value="taskPercent(runningTask)" color="primary" rounded height="8" class="mt-3" /></VCardText>
    </VCard>

    <VTabs v-model="tab" class="section-tabs" grow>
      <VTab value="overview" prepend-icon="mdi-view-dashboard-outline">总览</VTab>
      <VTab value="records" prepend-icon="mdi-format-list-checks">待纠正</VTab>
      <VTab value="audits" prepend-icon="mdi-clipboard-text-clock-outline">操作记录</VTab>
      <VTab value="settings" prepend-icon="mdi-cog-outline">设置</VTab>
    </VTabs>

    <VWindow v-model="tab">
      <VWindowItem value="overview">
        <section class="stat-grid" aria-label="整理纠正统计">
          <VCard v-for="item in [
            {label:'全部记录',key:'total',icon:'mdi-folder-search-outline'},
            {label:'可批量纠正',key:'ready',icon:'mdi-check-decagram-outline'},
            {label:'需人工确认',key:'manual',icon:'mdi-account-search-outline'},
            {label:'待清理',key:'cleanup_pending',icon:'mdi-broom'},
            {label:'已纠正',key:'corrected',icon:'mdi-folder-check-outline'},
          ]" :key="item.key" variant="outlined" class="stat-card"><VCardText><VIcon :icon="item.icon" size="25" /><strong>{{ bootstrap.stats?.[item.key] || 0 }}</strong><span>{{ item.label }}</span></VCardText></VCard>
        </section>
        <VCard variant="outlined" class="content-card mt-4">
          <VCardTitle>安全处理顺序</VCardTitle>
          <VCardText><div class="workflow-grid"><div><VIcon icon="mdi-magnify-scan" /><b>1. 严格匹配</b><span>中文片名、年份、类型唯一命中。</span></div><div><VIcon icon="mdi-eye-outline" /><b>2. 强制预览</b><span>确认旧英文路径与新中文路径。</span></div><div><VIcon icon="mdi-folder-check-outline" /><b>3. 验证新媒体</b><span>新记录、媒体 ID 和目标文件均存在。</span></div><div><VIcon icon="mdi-shield-lock-outline" /><b>4. 清理旧目标</b><span>只删除记录中的旧目标，永不删除源文件。</span></div></div></VCardText>
        </VCard>
        <VAlert type="info" variant="tonal" class="mt-4" icon="mdi-clock-check-outline">上次扫描：{{ bootstrap.last_scan_at || '尚未扫描' }}。电视剧会列出，但首版必须人工确认季集。</VAlert>
      </VWindowItem>

      <VWindowItem value="records">
        <section class="section-heading"><div><h2>英文整理记录</h2><p>批量纠正只接受唯一精确匹配的电影；其他记录请人工选择候选。</p></div><div class="button-row"><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-radar" :disabled="hasRunningTask" @click="scan(false)">增量扫描</VBtn><VBtn class="action-btn" variant="text" prepend-icon="mdi-database-refresh-outline" :disabled="hasRunningTask" @click="scan(true)">全量重扫</VBtn></div></section>
        <div class="filter-row"><VTextField v-model="records.keyword" label="搜索标题或路径" prepend-inner-icon="mdi-magnify" clearable hide-details @keyup.enter="records.page=1;loadRecords()" /><VSelect v-model="records.state" label="处理状态" :items="stateItems" hide-details @update:model-value="records.page=1;loadRecords()" /><VSelect v-model="records.media_type" label="媒体类型" :items="[{title:'全部',value:''},{title:'电影',value:'电影'},{title:'电视剧',value:'电视剧'}]" hide-details @update:model-value="records.page=1;loadRecords()" /><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-filter-check-outline" @click="records.page=1;loadRecords()">筛选</VBtn></div>
        <div class="selection-bar"><span>已选择 {{ selected.length }} 条<span v-if="selected.length>10">（批量纠正最多 10 条）</span></span><VSpacer /><VBtn variant="text" prepend-icon="mdi-eye-off-outline" :disabled="!selected.length" @click="setIgnored(records.state !== 'ignored')">{{ records.state === 'ignored' ? '恢复' : '忽略' }}</VBtn><VBtn color="primary" variant="tonal" prepend-icon="mdi-folder-sync-outline" :disabled="!canBatch" :loading="batchLoading" @click="openBatch">批量纠正</VBtn><VBtn color="error" variant="text" prepend-icon="mdi-delete-outline" :disabled="!selected.length" @click="openDelete">删除</VBtn></div>

        <VDataTable v-model="selected" :headers="recordHeaders" :items="records.items" item-value="history_id" :loading="loading" show-select class="desktop-table" :items-per-page="50" hide-default-footer>
          <template #item.old_title="{ item }"><div class="title-cell"><strong>{{ item.old_title }}</strong><span>{{ item.old_year || '年份未知' }} · {{ item.media_type || '类型未知' }}</span><small :title="item.old_dest">{{ item.old_dest }}</small></div></template>
          <template #item.query_title="{ item }"><div class="title-cell"><strong>{{ item.query_title || '未提取到中文片名' }}</strong><span>{{ item.query_year || '年份未知' }} · {{ item.mode || '模式未知' }}</span><small class="source-safe" :title="item.src"><VIcon icon="mdi-lock-outline" size="14" />源文件保留</small></div></template>
          <template #item.candidate="{ item }"><div v-if="item.candidate?.media_id" class="candidate-inline"><VAvatar rounded="lg" size="48"><VImg v-if="item.candidate.poster_url" :src="item.candidate.poster_url" :alt="`${item.candidate.title} 海报`" cover loading="lazy" /><VIcon v-else icon="mdi-movie-open-outline" /></VAvatar><div><strong>{{ item.candidate.title }}</strong><span>{{ item.candidate.year }} · {{ item.candidate.original_title || item.candidate.media_source }}</span></div></div><span v-else class="muted">尚无唯一候选</span></template>
          <template #item.state="{ item }"><VChip :color="stateMeta(item.state,item.ignored).color" :prepend-icon="stateMeta(item.state,item.ignored).icon" variant="tonal" size="small">{{ stateMeta(item.state,item.ignored).text }}</VChip><small class="reason">{{ item.reason }}</small></template>
          <template #item.actions="{ item }"><div class="row-actions"><VBtn icon="mdi-account-search-outline" variant="text" aria-label="人工匹配" @click="openManual(item)" /><VBtn v-if="item.state==='cleanup_pending'" icon="mdi-broom" color="warning" variant="text" aria-label="重试清理旧媒体" @click="retryCleanup(item)" /></div></template>
          <template #bottom><VPagination v-if="records.total>50" v-model="records.page" :length="Math.ceil(records.total/50)" total-visible="7" class="pa-2" @update:model-value="loadRecords" /></template>
        </VDataTable>

        <div class="mobile-list">
          <VCard v-for="item in records.items" :key="item.history_id" variant="outlined" class="mobile-card"><VCardText><div class="mobile-card-head"><VCheckboxBtn v-model="selected" :value="item.history_id" :aria-label="`选择 ${item.old_title}`" /><VChip :color="stateMeta(item.state,item.ignored).color" variant="tonal" size="small">{{ stateMeta(item.state,item.ignored).text }}</VChip></div><strong class="mobile-title">{{ item.old_title }} <span>({{ item.old_year || '—' }})</span></strong><p>中文源：{{ item.query_title || '未提取' }} ({{ item.query_year || '—' }})</p><p>候选：{{ item.candidate?.title || '需人工搜索' }}</p><small class="source-safe"><VIcon icon="mdi-lock-outline" size="14" />源文件永久保留</small><div class="button-row mt-3"><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-account-search-outline" @click="openManual(item)">人工匹配</VBtn><VBtn v-if="item.state==='cleanup_pending'" class="action-btn" color="warning" variant="text" prepend-icon="mdi-broom" @click="retryCleanup(item)">重试清理</VBtn></div></VCardText></VCard>
          <VAlert v-if="!records.items.length && !loading" type="info" variant="tonal">没有符合当前筛选条件的记录。可以先执行增量扫描或全量重扫。</VAlert>
        </div>
      </VWindowItem>

      <VWindowItem value="audits">
        <section class="section-heading"><div><h2>操作审计</h2><p>保留旧标题、旧路径、新标题、新路径以及清理结果。</p></div><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-refresh" @click="loadAudits">刷新</VBtn></section>
        <VDataTable :headers="auditHeaders" :items="audits.items" class="desktop-table" :items-per-page="50" hide-default-footer><template #item.status="{item}"><VChip :color="auditStatus(item.status).color" variant="tonal" size="small">{{ auditStatus(item.status).text }}</VChip></template><template #bottom><VPagination v-if="audits.total>50" v-model="audits.page" :length="Math.ceil(audits.total/50)" class="pa-2" @update:model-value="loadAudits" /></template></VDataTable>
        <div class="mobile-list"><VCard v-for="item in audits.items" :key="item.id" variant="outlined"><VCardText><div class="mobile-card-head"><strong>{{ item.action }}</strong><VChip :color="auditStatus(item.status).color" variant="tonal" size="small">{{ auditStatus(item.status).text }}</VChip></div><p>{{ item.old_title || '—' }} → {{ item.new_title || '—' }}</p><small>{{ item.created_at }}</small><p>{{ item.message }}</p></VCardText></VCard></div>
      </VWindowItem>

      <VWindowItem value="settings">
        <section class="settings-hero"><div><span class="eyebrow">SCHEDULE & SAFETY</span><h2>扫描与安全设置</h2><p>自动纠正默认关闭；即使开启也只处理唯一精确匹配的电影。</p></div><VBtn class="action-btn" color="primary" prepend-icon="mdi-content-save" :loading="loading" @click="saveSettings">保存设置</VBtn></section>
        <VCard variant="outlined" class="settings-card"><VCardText><div class="settings-grid"><VSwitch v-model="bootstrap.config.enabled" label="启用插件与 Cron" color="primary" hide-details /><VSwitch v-model="bootstrap.config.notify_enabled" label="发送任务汇总通知" color="primary" hide-details /><VTextField v-model="bootstrap.config.scan_cron" label="扫描 Cron" hint="五段 Cron 表达式，例如 0 4 * * *" persistent-hint /><VTextField v-model.number="bootstrap.config.auto_batch_limit" type="number" min="1" max="50" label="单次自动纠正上限" /><VSwitch v-model="bootstrap.config.auto_correct" label="允许定时自动纠正精确电影" color="primary" hide-details /><VSwitch v-model="bootstrap.config.cleanup_old_after_correct" label="新整理验证成功后清理旧英文媒体" color="primary" hide-details /></div><VAlert :type="bootstrap.cron_preview?.valid ? 'success' : 'warning'" variant="tonal" class="mt-4">{{ bootstrap.cron_preview?.text || '尚未计算执行时间' }}</VAlert><div class="settings-actions mt-4"><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-bell-check-outline" @click="testNotification">发送测试通知</VBtn><VBtn class="action-btn" variant="text" prepend-icon="mdi-database-refresh-outline" :disabled="hasRunningTask" @click="scan(true)">立即全量重扫</VBtn></div></VCardText></VCard>
      </VWindowItem>
    </VWindow>

    <VDialog v-model="manualDialog" max-width="980" scrollable>
      <VCard><VCardTitle class="dialog-title"><div><span>人工匹配与整理预览</span><small>{{ manual.record?.old_title }} → 中文整理结果</small></div><VBtn icon="mdi-close" variant="text" aria-label="关闭" @click="manualDialog=false" /></VCardTitle><VCardText><VAlert type="success" variant="tonal" icon="mdi-shield-lock-outline" class="mb-4">源文件为只读保护对象。插件没有删除源文件的接口。</VAlert><div class="manual-search"><VTextField v-model="manual.title" label="源文件中文片名" /><VTextField v-model.number="manual.year" type="number" min="1888" max="2100" label="年份" /><VSelect v-model="manual.media_type" label="媒体类型" :items="['电影','电视剧']" /><VBtn class="action-btn" color="primary" variant="tonal" prepend-icon="mdi-movie-search-outline" :loading="manualLoading" @click="searchManual">搜索</VBtn></div><VProgressLinear v-if="manualLoading" indeterminate color="primary" class="my-3" aria-label="正在搜索或预览" /><div v-if="manual.candidates.length" class="candidate-grid"><button v-for="candidate in manual.candidates" :key="`${candidate.media_source}:${candidate.media_id}:${candidate.media_type}`" type="button" class="candidate-card" :class="{'candidate-card--selected':manual.candidate?.media_id===candidate.media_id && manual.candidate?.media_source===candidate.media_source}" :aria-pressed="manual.candidate?.media_id===candidate.media_id && manual.candidate?.media_source===candidate.media_source" @click="chooseCandidate(candidate)"><div class="poster-wrap"><VImg v-if="candidate.poster_url" :src="candidate.poster_url" :alt="`${candidate.title} 海报`" aspect-ratio="2/3" cover loading="lazy" /><VIcon v-else icon="mdi-movie-open-outline" size="42" /></div><strong>{{ candidate.title }}</strong><span>{{ candidate.year || '年份未知' }}</span><small>{{ candidate.original_title || `${candidate.media_source}:${candidate.media_id}` }}</small></button></div><VAlert v-else type="info" variant="tonal">修改中文片名和年份后搜索，选择一个明确候选。</VAlert><VCard v-if="manual.preview" variant="outlined" class="preview-card mt-4"><VCardText><h3>整理路径预览</h3><div class="path-diff"><div><span>旧英文目标</span><code>{{ manual.preview.old_target?.path }}</code></div><VIcon icon="mdi-arrow-right" /><div><span>新中文目标</span><code>{{ manual.preview.new_target?.path }}</code></div></div><VSwitch v-model="manual.cleanup_old" label="新目标验证成功后删除旧英文媒体" color="primary" hide-details class="mt-3" /></VCardText></VCard></VCardText><VCardActions><VBtn variant="text" @click="manualDialog=false">取消</VBtn><VSpacer /><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-eye-outline" :disabled="!manual.candidate" :loading="manualLoading" @click="previewManual">预览路径</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-folder-sync-outline" :disabled="!manual.preview" @click="correctManual">确认重新整理</VBtn></VCardActions></VCard>
    </VDialog>

    <VDialog v-model="batchDialog" max-width="620">
      <VCard><VCardTitle>确认批量重新整理</VCardTitle><VCardText><VAlert type="success" variant="tonal" icon="mdi-lock-outline" class="mb-4">已逐条完成 {{ batchPreviews.length }} 条路径预览。源文件不会被删除或移动。</VAlert><div class="batch-preview-list"><div v-for="item in batchPreviews" :key="item.history_id"><span>{{ item.candidate?.title }} ({{ item.candidate?.year }})</span><code>{{ item.old_target?.path }}</code><VIcon icon="mdi-arrow-down" size="18" /><code>{{ item.new_target?.path }}</code></div></div><VSwitch v-model="manual.cleanup_old" label="新媒体与新记录验证成功后删除旧英文媒体" color="primary" /><p class="muted">任意记录失败时，该记录的旧媒体保持不动，原整理记录会自动恢复。</p></VCardText><VCardActions><VSpacer /><VBtn variant="text" @click="batchDialog=false">取消</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-folder-sync-outline" :disabled="batchPreviews.length!==batchableRecords.length" @click="submitBatch">开始纠正</VBtn></VCardActions></VCard>
    </VDialog>

    <VDialog v-model="deleteDialog" max-width="620">
      <VCard><VCardTitle class="text-error">删除旧整理数据</VCardTitle><VCardText><VAlert type="error" variant="tonal" class="mb-4">操作仅针对整理记录中保存的旧目标路径。源路径不可编辑，也不会传入任何删除方法。</VAlert><VCheckbox v-model="deletion.delete_media" label="删除旧已整理媒体文件" color="error" /><VCheckbox v-model="deletion.delete_history" label="删除原 MoviePilot 整理记录" color="error" /><VDivider class="my-3" /><VCheckbox v-model="deletion.source_safe_confirmed" label="我已确认：源文件必须永久保留" color="primary" /></VCardText><VCardActions><VSpacer /><VBtn variant="text" @click="deleteDialog=false">取消</VBtn><VBtn class="action-btn" color="error" prepend-icon="mdi-delete-alert-outline" :disabled="(!deletion.delete_media&&!deletion.delete_history)||!deletion.source_safe_confirmed" @click="submitDelete">确认删除旧目标</VBtn></VCardActions></VCard>
    </VDialog>
  </main>
</template>

<style scoped>
.correct-app { --line: rgba(var(--v-border-color), calc(var(--v-border-opacity) + .08)); --soft: rgba(var(--v-theme-surface-variant), .22); --muted: rgba(var(--v-theme-on-surface), .68); max-width: 1600px; margin: 0 auto; color: rgb(var(--v-theme-on-surface)); font-variant-numeric: tabular-nums; }
.page-header,.page-brand,.section-heading,.button-row,.selection-bar,.task-heading,.mobile-card-head,.settings-hero,.settings-actions { display:flex; align-items:center; }
.page-header { justify-content:space-between; gap:20px; padding:16px 18px; margin-bottom:14px; border:1px solid var(--line); border-radius:14px; background:linear-gradient(110deg,rgba(var(--v-theme-primary),.11),rgb(var(--v-theme-surface)) 44%); box-shadow:0 8px 24px rgba(var(--v-theme-on-surface),.045); }
.page-brand { gap:14px; min-width:0; }.brand-icon { display:grid; place-items:center; flex:0 0 46px; width:46px; height:46px; border-radius:12px; color:rgb(var(--v-theme-primary)); background:rgba(var(--v-theme-primary),.12); border:1px solid rgba(var(--v-theme-primary),.18); }
h1 { margin:2px 0 3px; font-size:clamp(1.45rem,2.4vw,2rem); line-height:1.15; letter-spacing:-.025em; }h2 { margin:0 0 3px; font-size:1.2rem; }h3 { margin:0 0 10px; font-size:1rem; }p { margin:0; color:var(--muted); line-height:1.5; }.eyebrow { color:rgb(var(--v-theme-primary)); font-size:.67rem; font-weight:800; letter-spacing:.13em; }.muted { color:var(--muted); }
.action-btn,.correct-app :deep(.v-btn) { min-height:44px; }.correct-app :deep(.v-field) { border-radius:10px; }.correct-app :deep(.v-chip) { border-radius:8px; }
.section-tabs { min-height:52px; margin-bottom:16px; padding:3px; border:1px solid var(--line); border-radius:12px; background:rgb(var(--v-theme-surface)); }.section-tabs :deep(.v-tab) { min-height:44px; border-radius:9px; text-transform:none; font-weight:650; }.section-tabs :deep(.v-tab.v-tab--selected) { background:rgba(var(--v-theme-primary),.11); }
.task-card { border-color:rgba(var(--v-theme-primary),.35); }.task-heading { justify-content:space-between; gap:14px; }.task-heading>div { display:grid; gap:3px; }.task-heading small { color:var(--muted); }
.stat-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }.stat-card,.content-card,.settings-card,.mobile-card { border-color:var(--line); border-radius:12px; background:rgb(var(--v-theme-surface)); }.stat-card :deep(.v-card-text) { display:grid; grid-template-columns:44px minmax(0,1fr); grid-template-rows:auto auto; align-items:center; gap:0 10px; padding:14px; }.stat-card .v-icon { grid-row:1/3; padding:8px; border-radius:10px; color:rgb(var(--v-theme-primary)); background:rgba(var(--v-theme-primary),.1); }.stat-card strong { font-size:1.42rem; }.stat-card span { color:var(--muted); font-size:.8rem; }
.content-card :deep(.v-card-title) { padding:14px 16px 8px; font-size:1rem; font-weight:700; }.workflow-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); }.workflow-grid>div { display:grid; grid-template-columns:24px 1fr; gap:4px 8px; padding:4px 16px; border-left:1px solid var(--line); }.workflow-grid>div:first-child { padding-left:0; border-left:0; }.workflow-grid .v-icon { grid-row:1/3; color:rgb(var(--v-theme-primary)); }.workflow-grid span { color:var(--muted); font-size:.8rem; line-height:1.45; }
.section-heading { justify-content:space-between; align-items:flex-start; gap:18px; margin-bottom:14px; }.section-heading p { max-width:850px; font-size:.88rem; }.button-row,.settings-actions { flex-wrap:wrap; gap:8px; }.filter-row { display:grid; grid-template-columns:2fr 1fr 1fr auto; gap:10px; margin:12px 0; }.selection-bar { min-height:52px; gap:8px; padding:4px 10px; margin-bottom:10px; border:1px solid var(--line); border-radius:11px; background:var(--soft); }.selection-bar>span { color:var(--muted); font-size:.84rem; }
.desktop-table { overflow:hidden; border:1px solid var(--line); border-radius:12px; background:rgb(var(--v-theme-surface)); }.desktop-table :deep(thead) { background:var(--soft); }.desktop-table :deep(.v-data-table__td),.desktop-table :deep(.v-data-table__th) { padding-inline:12px; }.title-cell { display:grid; gap:3px; padding-block:7px; }.title-cell span,.title-cell small,.candidate-inline span,.reason { display:block; color:var(--muted); font-size:.76rem; }.title-cell small { max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.source-safe { display:inline-flex!important; align-items:center; gap:3px; color:rgb(var(--v-theme-success))!important; }.candidate-inline { display:flex; align-items:center; gap:10px; padding-block:6px; }.candidate-inline>div:last-child { min-width:0; }.candidate-inline strong { display:block; }.reason { max-width:190px; margin-top:5px; line-height:1.35; }.row-actions { display:flex; gap:4px; }
.mobile-list { display:none; }.mobile-card-head { justify-content:space-between; }.mobile-title { display:block; margin-top:8px; }.mobile-title span { color:var(--muted); font-weight:400; }.mobile-card p,.mobile-card small { display:block; margin-top:7px; overflow-wrap:anywhere; }
.settings-hero { justify-content:space-between; gap:18px; padding:14px 16px; margin-bottom:12px; border:1px solid rgba(var(--v-theme-primary),.3); border-radius:12px; background:linear-gradient(120deg,rgba(var(--v-theme-primary),.09),rgb(var(--v-theme-surface)) 56%); }.settings-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }.settings-actions { justify-content:flex-end; }
.dialog-title { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; }.dialog-title>div { display:grid; gap:2px; }.dialog-title small { color:var(--muted); font-size:.78rem; font-weight:400; white-space:normal; }.manual-search { display:grid; grid-template-columns:2fr 1fr 1fr auto; align-items:center; gap:10px; }.candidate-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; }.candidate-card { min-height:44px; padding:0; overflow:hidden; color:inherit; text-align:left; border:1px solid var(--line); border-radius:11px; background:rgb(var(--v-theme-surface)); cursor:pointer; transition:border-color 180ms ease,box-shadow 180ms ease; }.candidate-card:hover,.candidate-card:focus-visible,.candidate-card--selected { border-color:rgb(var(--v-theme-primary)); box-shadow:0 0 0 2px rgba(var(--v-theme-primary),.18); outline:0; }.candidate-card strong,.candidate-card span,.candidate-card small { display:block; padding-inline:10px; }.candidate-card strong { margin-top:9px; }.candidate-card span,.candidate-card small { color:var(--muted); font-size:.76rem; }.candidate-card small { min-height:2.8em; padding-bottom:10px; }.poster-wrap { display:grid; place-items:center; aspect-ratio:2/3; color:var(--muted); background:var(--soft); }.preview-card { border-color:rgba(var(--v-theme-primary),.35); }.path-diff { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:12px; }.path-diff>div { display:grid; gap:5px; min-width:0; }.path-diff span { color:var(--muted); font-size:.78rem; }.path-diff code { padding:9px; overflow-wrap:anywhere; border-radius:8px; background:var(--soft); font-size:.78rem; }
.batch-preview-list { display:grid; gap:10px; max-height:300px; margin-bottom:12px; overflow-y:auto; }.batch-preview-list>div { display:grid; gap:5px; padding:10px; border:1px solid var(--line); border-radius:10px; background:var(--soft); }.batch-preview-list span { font-weight:700; }.batch-preview-list code { overflow-wrap:anywhere; color:var(--muted); font-size:.76rem; }
button:focus-visible,a:focus-visible { outline:3px solid rgba(var(--v-theme-primary),.72); outline-offset:2px; }
@media(max-width:1100px){.stat-grid{grid-template-columns:repeat(3,1fr)}.workflow-grid{grid-template-columns:repeat(2,1fr);gap:14px 0}.workflow-grid>div:nth-child(3){padding-left:0;border-left:0}.filter-row,.manual-search{grid-template-columns:1fr 1fr}.filter-row>.v-btn,.manual-search>.v-btn{width:100%}}
@media(max-width:700px){.correct-app{padding-inline:12px!important}.page-header,.section-heading,.selection-bar,.settings-hero{align-items:stretch;flex-direction:column}.page-header{padding:14px}.page-header>.v-btn{width:100%}.page-brand{align-items:flex-start}.brand-icon{flex-basis:42px;width:42px;height:42px}.stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.workflow-grid,.settings-grid,.filter-row,.manual-search,.path-diff{grid-template-columns:1fr}.workflow-grid>div,.workflow-grid>div:nth-child(3){padding:10px 0;border-top:1px solid var(--line);border-left:0}.workflow-grid>div:first-child{border-top:0}.path-diff>.v-icon{transform:rotate(90deg);justify-self:center}.selection-bar{padding:8px 10px}.desktop-table{display:none}.mobile-list{display:grid;gap:8px}.candidate-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.settings-actions{align-items:stretch;flex-direction:column}.settings-actions>.v-btn{width:100%}}
@media(max-width:390px){.brand-icon{display:none}.candidate-grid{grid-template-columns:1fr 1fr}}
@media(prefers-reduced-motion:reduce){.candidate-card{transition:none}}
</style>
