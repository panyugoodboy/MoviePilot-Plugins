<script setup>
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const props = defineProps({
  api: { type: Object, default: () => ({}) },
  navKey: { type: String, default: 'main' },
  pluginId: { type: String, default: 'EmbyLibraryDownload' },
})

const toast = inject('moviepilot:toast', null)
const pluginId = computed(() => props.pluginId || 'EmbyLibraryDownload')
const endpoint = path => `plugin/${pluginId.value}${path}`
const tab = ref('overview')
const loading = ref(false)
const actionError = ref('')
const bootstrap = reactive({
  config: { quality_save_paths: {}, exclude_tv: true },
  options: { sites: [], emby_servers: [] },
  stats: {},
  tasks: {},
})
const inventory = reactive({ items: [], total: 0, page: 1, keyword: '', media_type: '' })
const targets = ref([])
const candidates = reactive({ items: [], total: 0, page: 1, keyword: '', site_id: null, scope: 'pool' })
const selectedCandidates = ref([])
const jobs = reactive({ items: [], total: 0, page: 1 })
const confirmDownload = ref(false)
const targetDialog = ref(false)
const editingTargetId = ref(null)
const targetForm = reactive(emptyTarget())
let pollTimer = null

const siteItems = computed(() => bootstrap.options.sites || [])
const serverItems = computed(() => (bootstrap.options.emby_servers || []).map(item => item.name))
const candidateScopeLabel = computed(() => {
  if (candidates.scope === 'pool') return '全站种子池'
  const id = Number(candidates.scope.split(':')[1])
  const target = targets.value.find(item => item.id === id)
  return target ? `${target.title} 的候选资源` : '目标候选资源'
})
const hasRunningTask = computed(() => Object.values(bootstrap.tasks || {}).some(task => task.status === 'running'))
const poolTask = computed(() => bootstrap.tasks?.pool || null)
const poolProgress = computed(() => poolTask.value?.progress || {})
const pageCandidateKeys = computed(() => candidates.items.map(item => item.candidate_key))
const allPageSelected = computed(() => pageCandidateKeys.value.length > 0 && pageCandidateKeys.value.every(key => selectedCandidates.value.includes(key)))

const inventoryHeaders = [
  { title: '年份', key: 'year', width: 80 },
  { title: '媒体', key: 'title', minWidth: 220 },
  { title: '季集', key: 'episode_label', width: 110, sortable: false },
  { title: '版本', key: 'quality_label', minWidth: 190, sortable: false },
  { title: '码率', key: 'bitrate_mbps', width: 100 },
  { title: '路径', key: 'path', minWidth: 260, sortable: false },
]
const candidateHeaders = [
  { title: '年份', key: 'year', width: 80 },
  { title: '种子', key: 'title', minWidth: 320, sortable: false },
  { title: '站点', key: 'site_name', width: 120 },
  { title: '质量', key: 'quality_label', minWidth: 210, sortable: false },
  { title: '码率', key: 'bitrate_mbps', width: 90 },
  { title: '大小', key: 'size_bytes', width: 100, sortable: false },
  { title: '做种', key: 'seeders', width: 80 },
]
const jobHeaders = [
  { title: '时间', key: 'created_at', width: 180 },
  { title: '种子', key: 'title', minWidth: 300, sortable: false },
  { title: '站点', key: 'site_name', width: 120 },
  { title: '状态', key: 'status', width: 110 },
  { title: '来源', key: 'automatic', width: 90 },
  { title: '结果', key: 'result', minWidth: 180, sortable: false },
]
const qualityTypeItems = [
  { title: '原盘', value: 'bluray' },
  { title: 'DIY', value: 'diy' },
  { title: 'Remux', value: 'remux' },
  { title: 'Encode', value: 'encode' },
  { title: 'WEB-DL', value: 'webdl' },
  { title: '未知', value: 'unknown' },
]

async function call(method, path, payload, params) {
  try {
    const options = params ? { params } : undefined
    const response = method === 'get' || method === 'delete'
      ? await props.api[method](endpoint(path), options)
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
  actionError.value = ''
  try {
    const data = await call('get', '/bootstrap')
    Object.assign(bootstrap, data || {})
  } finally {
    if (showLoading) loading.value = false
  }
}

async function loadOverview() {
  const data = await call('get', '/overview')
  bootstrap.stats = data?.stats || {}
  bootstrap.tasks = data?.tasks || {}
}

async function loadInventory() {
  loading.value = true
  try {
    const data = await call('get', '/inventory', null, {
      page: inventory.page,
      page_size: 50,
      keyword: inventory.keyword,
      media_type: inventory.media_type || '',
    })
    Object.assign(inventory, { items: data.items, total: data.total })
  } finally {
    loading.value = false
  }
}

async function loadTargets() {
  targets.value = await call('get', '/targets') || []
}

async function loadCandidates() {
  loading.value = true
  selectedCandidates.value = []
  try {
    const data = await call('get', '/candidates', null, {
      page: candidates.page,
      page_size: 50,
      scope: candidates.scope,
      keyword: candidates.keyword,
      site_id: candidates.site_id || undefined,
      eligible_only: true,
    })
    Object.assign(candidates, { items: data.items, total: data.total })
  } finally {
    loading.value = false
  }
}

async function loadJobs() {
  const data = await call('get', '/jobs', null, { page: jobs.page, page_size: 50 })
  Object.assign(jobs, { items: data.items, total: data.total })
}

async function runTask(path, successMessage) {
  await call('post', path, {})
  toast?.info?.(successMessage)
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
        await refreshCurrentTab()
      }
    } catch (_) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }, 1000)
}

async function refreshCurrentTab() {
  if (tab.value === 'inventory') return loadInventory()
  if (tab.value === 'targets') return loadTargets()
  if (tab.value === 'pool') return loadCandidates()
  if (tab.value === 'jobs') return loadJobs()
  return loadOverview()
}

function togglePageSelection() {
  selectedCandidates.value = allPageSelected.value ? [] : [...pageCandidateKeys.value]
}

async function submitDownloads() {
  const keys = [...selectedCandidates.value]
  confirmDownload.value = false
  await call('post', '/downloads', { candidate_keys: keys })
  selectedCandidates.value = []
  toast?.success?.(`已开始处理 ${keys.length} 个候选种子`)
  await loadOverview()
  startPolling()
}

function emptyTarget() {
  return {
    media_type: 'movie', media_source: 'themoviedb', media_id: '', title: '', year: null,
    seasons_text: '', desired_versions: 3, sites: [], profile: {}, save_path: '',
    auto_download: false, enabled: true,
  }
}

function openTarget(target = null) {
  editingTargetId.value = target?.id || null
  Object.assign(targetForm, emptyTarget(), target || {}, {
    seasons_text: (target?.seasons || []).join(','),
  })
  targetDialog.value = true
}

async function saveTarget() {
  const payload = {
    ...targetForm,
    seasons: String(targetForm.seasons_text || '').split(',').map(value => Number(value.trim())).filter(Boolean),
  }
  delete payload.seasons_text
  if (editingTargetId.value) await call('put', `/targets/${editingTargetId.value}`, payload)
  else await call('post', '/targets', payload)
  targetDialog.value = false
  toast?.success?.('目标已保存')
  await loadTargets()
}

async function deleteTarget(target) {
  if (!window.confirm(`确认删除目标“${target.title}”？`)) return
  await call('delete', `/targets/${target.id}`)
  toast?.success?.('目标已删除')
  await loadTargets()
}

async function searchTarget(target = null) {
  await call('post', '/targets/search', { target_ids: target ? [target.id] : [] })
  toast?.info?.('目标搜索已开始')
  await loadOverview()
  startPolling()
}

async function showTargetCandidates(target) {
  candidates.scope = `target:${target.id}`
  candidates.page = 1
  tab.value = 'pool'
  await loadCandidates()
}

async function saveSettings() {
  loading.value = true
  try {
    const response = await props.api.put(`plugin/${pluginId.value}`, bootstrap.config)
    if (response?.success === false) throw new Error(response.message || '保存失败')
    toast?.success?.('设置已保存并重新加载插件')
    await loadBootstrap(false)
  } catch (error) {
    const message = error?.response?.data?.message || error?.message || '保存失败'
    actionError.value = message
    toast?.error?.(message)
  } finally {
    loading.value = false
  }
}

function setServerLibraries(serverName, libraries) {
  if (!bootstrap.config.emby_libraries) bootstrap.config.emby_libraries = {}
  bootstrap.config.emby_libraries[serverName] = libraries
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`
}

function safeUrl(value) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch (_) {
    return ''
  }
}

function qualityLabel(item) {
  return [item.quality_type, item.quality_effect, item.resolution, item.video_codec]
    .filter(value => value && value !== 'unknown').join(' · ') || '未识别'
}

function episodeLabel(item) {
  if (item.item_type !== 'tv') return '电影'
  return `S${String(item.season ?? 0).padStart(2, '0')}E${String(item.episode ?? 0).padStart(2, '0')}`
}

function statusColor(status) {
  return { success: 'success', queued: 'info', present: 'success', reserved: 'warning', running: 'primary', failed: 'error', cancelled: 'default' }[status] || 'default'
}

watch(tab, refreshCurrentTab)
watch(() => [inventory.keyword, inventory.media_type], () => { inventory.page = 1 })
watch(() => [candidates.keyword, candidates.site_id], () => { candidates.page = 1 })

onMounted(async () => {
  await loadBootstrap()
  await loadTargets()
  if (hasRunningTask.value) startPolling()
})
onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<template>
  <main class="emby-app pa-3 pa-md-6">
    <header class="page-header mb-5">
      <div>
        <div class="eyebrow">EMBY LIBRARY CONTROL</div>
        <h1>联动 EMBY 库筛选下载</h1>
        <p>先核对已有版本，再按站点、质量和三版本上限补齐媒体库。</p>
      </div>
      <VBtn class="action-btn" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="refreshCurrentTab">
        刷新当前页
      </VBtn>
    </header>

    <VAlert v-if="actionError" type="error" variant="tonal" closable class="mb-4" @click:close="actionError = ''">
      {{ actionError }}
    </VAlert>
    <VProgressLinear v-if="loading" indeterminate color="primary" class="mb-3" aria-label="正在加载" />

    <VTabs v-model="tab" class="section-tabs mb-4" show-arrows aria-label="插件功能导航">
      <VTab value="overview">总览</VTab>
      <VTab value="inventory">版本库存</VTab>
      <VTab value="targets">目标清单</VTab>
      <VTab value="pool">站点种子池</VTab>
      <VTab value="jobs">下载任务</VTab>
      <VTab value="settings">规则设置</VTab>
    </VTabs>

    <VWindow v-model="tab">
      <VWindowItem value="overview">
        <section aria-labelledby="overview-title">
          <div class="section-heading">
            <div><h2 id="overview-title">媒体库状态</h2><p>统计来自插件本地版本库与受控下载队列。</p></div>
            <div class="button-row">
              <VBtn class="action-btn" color="primary" prepend-icon="mdi-database-sync" @click="runTask('/inventory/sync', 'Emby 同步已开始')">同步 EMBY</VBtn>
              <VBtn class="action-btn" variant="tonal" prepend-icon="mdi-magnify" @click="searchTarget()">搜索全部目标</VBtn>
            </div>
          </div>
          <VAlert v-if="!bootstrap.stats.inventory_ready" type="warning" variant="tonal" class="mb-4">首次下载前必须先成功同步 Emby 版本库存。库存未知时，插件会拒绝手动和自动下载。</VAlert>
          <div class="stat-grid mb-5">
            <VCard v-for="item in [
              ['版本记录', bootstrap.stats.inventory_versions || 0, 'mdi-movie-open'],
              ['媒体单元', bootstrap.stats.media_items || 0, 'mdi-library'],
              ['启用目标', bootstrap.stats.active_targets || 0, 'mdi-target'],
              ['合格候选', bootstrap.stats.eligible_candidates || 0, 'mdi-filter-check'],
              ['队列占位', bootstrap.stats.active_jobs || 0, 'mdi-download-circle'],
            ]" :key="item[0]" variant="outlined" class="stat-card">
              <VCardText><VIcon :icon="item[2]" size="28" color="primary" /><strong>{{ item[1] }}</strong><span>{{ item[0] }}</span></VCardText>
            </VCard>
          </div>
          <VCard variant="outlined" class="content-card">
            <VCardTitle>运行链路</VCardTitle>
            <VCardText class="workflow-grid">
              <div><VIcon icon="mdi-database-eye" /><b>1. 读取版本</b><span>从 Emby MediaSources 建立电影和逐集版本库存。</span></div>
              <div><VIcon icon="mdi-filter-cog" /><b>2. 质量筛选</b><span>识别原盘、DIY、Remux、Encode、WEB-DL、DV、HDR 与码率。</span></div>
              <div><VIcon icon="mdi-shield-check" /><b>3. 原子限额</b><span>现有版本加下载中占位不得超过 1–3 个，默认拒绝同质量槽位。</span></div>
              <div><VIcon icon="mdi-download-network" /><b>4. 交付下载</b><span>按电影、电视剧或目标专用路径提交 MoviePilot 下载链。</span></div>
            </VCardText>
          </VCard>
          <VCard v-if="Object.keys(bootstrap.tasks || {}).length" variant="outlined" class="content-card mt-4">
            <VCardTitle>最近后台任务</VCardTitle>
            <VList lines="two">
              <VListItem v-for="(taskInfo, name) in bootstrap.tasks" :key="name" :title="name" :subtitle="taskInfo.message || taskInfo.started_at">
                <template #append><VChip :color="statusColor(taskInfo.status)" size="small">{{ taskInfo.status }}</VChip></template>
              </VListItem>
            </VList>
          </VCard>
        </section>
      </VWindowItem>

      <VWindowItem value="inventory">
        <section aria-labelledby="inventory-title">
          <div class="section-heading">
            <div><h2 id="inventory-title">EMBY 版本库存</h2><p>每个 MediaSource 记为一个版本，电视剧精确到季集。</p></div>
            <VBtn class="action-btn" color="primary" prepend-icon="mdi-database-sync" @click="runTask('/inventory/sync', 'Emby 同步已开始')">立即同步</VBtn>
          </div>
          <div class="filter-row">
            <VTextField v-model="inventory.keyword" label="搜索标题或路径" prepend-inner-icon="mdi-magnify" clearable hide-details @keyup.enter="loadInventory" />
            <VSelect v-model="inventory.media_type" label="媒体类型" :items="[{title:'全部',value:''},{title:'电影',value:'movie'},{title:'电视剧',value:'tv'}]" hide-details />
            <VBtn class="action-btn" variant="tonal" @click="loadInventory">筛选</VBtn>
          </div>
          <div class="desktop-table">
            <VDataTableServer :headers="inventoryHeaders" :items="inventory.items" :items-length="inventory.total" :items-per-page="50" :page="inventory.page" fixed-header hover @update:page="value => { inventory.page = value; loadInventory() }">
              <template #item.episode_label="{ item }">{{ episodeLabel(item) }}</template>
              <template #item.quality_label="{ item }"><VChip size="small" variant="tonal">{{ qualityLabel(item) }}</VChip></template>
              <template #item.bitrate_mbps="{ item }">{{ item.bitrate_mbps ? `${item.bitrate_mbps} Mbps` : '未知' }}</template>
              <template #item.path="{ item }"><span class="path-cell" :title="item.path">{{ item.path }}</span></template>
              <template #bottom><VPagination v-model="inventory.page" :length="Math.max(1, Math.ceil(inventory.total / 50))" @update:model-value="loadInventory" /></template>
            </VDataTableServer>
          </div>
          <div class="mobile-list">
            <VCard v-for="item in inventory.items" :key="item.version_key" variant="outlined" class="mobile-card">
              <VCardText><div class="mobile-title">{{ item.title }} <span>{{ item.year || '年份未知' }}</span></div><VChip size="small">{{ episodeLabel(item) }}</VChip><p>{{ qualityLabel(item) }} · {{ item.bitrate_mbps || '未知' }} Mbps</p><small>{{ item.path }}</small></VCardText>
            </VCard>
            <VPagination v-model="inventory.page" :length="Math.max(1, Math.ceil(inventory.total / 50))" @update:model-value="loadInventory" />
          </div>
        </section>
      </VWindowItem>

      <VWindowItem value="targets">
        <section aria-labelledby="targets-title">
          <div class="section-heading">
            <div><h2 id="targets-title">目标清单</h2><p>目标可单独指定站点、版本数、季和保存路径。</p></div>
            <div class="button-row"><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-magnify" @click="searchTarget()">搜索全部</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-plus" @click="openTarget()">新增目标</VBtn></div>
          </div>
          <VAlert v-if="!targets.length" type="info" variant="tonal">暂无目标。新增电影或剧集目标后，可手动搜索或启用自动下载。</VAlert>
          <div class="target-grid">
            <VCard v-for="target in targets" :key="target.id" variant="outlined" class="target-card">
              <VCardText>
                <div class="target-top"><div><span class="eyebrow">{{ target.media_type === 'tv' ? 'SERIES' : 'MOVIE' }}</span><h3>{{ target.title }} <small>{{ target.year || '' }}</small></h3></div><VChip :color="target.enabled ? 'success' : 'default'" size="small">{{ target.enabled ? '启用' : '停用' }}</VChip></div>
                <dl><div><dt>媒体标识</dt><dd>{{ target.media_source }}:{{ target.media_id || '标题识别' }}</dd></div><div><dt>版本目标</dt><dd>{{ target.desired_versions }} / 最大 3</dd></div><div><dt>自动下载</dt><dd>{{ target.auto_download ? '是' : '否' }}</dd></div></dl>
                <div class="button-row mt-4"><VBtn variant="tonal" size="small" @click="searchTarget(target)">搜索</VBtn><VBtn variant="text" size="small" @click="showTargetCandidates(target)">候选</VBtn><VBtn variant="text" size="small" @click="openTarget(target)">编辑</VBtn><VBtn variant="text" color="error" size="small" @click="deleteTarget(target)">删除</VBtn></div>
              </VCardText>
            </VCard>
          </div>
        </section>
      </VWindowItem>

      <VWindowItem value="pool">
        <section aria-labelledby="pool-title">
          <div class="section-heading">
            <div><h2 id="pool-title">{{ candidateScopeLabel }}</h2><p>按 UBits 的 WEB-DL、Remux、DIY 原盘、Encode 分类扫描全部页面；列表固定每页 50 条并按年份倒序。</p></div>
            <div class="button-row"><VBtn v-if="candidates.scope !== 'pool'" class="action-btn" variant="text" @click="candidates.scope='pool'; candidates.page=1; loadCandidates()">返回全站</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-radar" :loading="poolTask?.status === 'running'" @click="runTask('/pool/refresh', 'UBits 电影分类刷新已开始')">刷新 UBits 电影分类</VBtn></div>
          </div>
          <VAlert v-if="poolTask && (poolTask.status === 'running' || poolProgress.completed_pages)" :type="poolTask?.status === 'running' ? 'info' : poolTask?.status === 'failed' ? 'error' : 'success'" variant="tonal" class="mb-4">
            <div class="d-flex justify-space-between flex-wrap ga-2 mb-2">
              <strong>{{ poolTask?.status === 'running' ? poolTask.message : `上次刷新：${poolTask?.message || '已完成'}` }}</strong>
              <span>已扫描 {{ poolProgress.completed_pages || 0 }} 页 · 完成 {{ poolProgress.completed_sources || 0 }} / {{ poolProgress.total_sources || 4 }} 个分类</span>
            </div>
            <VProgressLinear :model-value="poolProgress.percent || 0" color="primary" height="8" rounded />
            <small>已发现 {{ poolProgress.found || 0 }} 个候选，其中 {{ poolProgress.eligible || 0 }} 个符合规则</small>
          </VAlert>
          <div class="filter-row">
            <VTextField v-model="candidates.keyword" label="搜索种子标题" prepend-inner-icon="mdi-magnify" clearable hide-details @keyup.enter="loadCandidates" />
            <VSelect v-model="candidates.site_id" label="站点" :items="siteItems" item-title="name" item-value="id" clearable hide-details />
            <VBtn class="action-btn" variant="tonal" @click="loadCandidates">筛选</VBtn>
          </div>
          <div class="selection-bar">
            <VCheckbox :model-value="allPageSelected" :indeterminate="selectedCandidates.length > 0 && !allPageSelected" hide-details label="当前页全选" @update:model-value="togglePageSelection" />
            <span>已选 {{ selectedCandidates.length }} / 当前页 {{ candidates.items.length }}</span>
            <VSpacer />
            <VBtn class="action-btn" color="primary" prepend-icon="mdi-download-multiple" :disabled="!selectedCandidates.length || !bootstrap.stats.inventory_ready" @click="confirmDownload = true">下载已选</VBtn>
          </div>
          <div class="desktop-table">
            <VDataTableServer v-model="selectedCandidates" show-select item-value="candidate_key" :headers="candidateHeaders" :items="candidates.items" :items-length="candidates.total" :items-per-page="50" :page="candidates.page" fixed-header hover @update:page="value => { candidates.page = value; loadCandidates() }">
              <template #item.title="{ item }"><a v-if="safeUrl(item.page_url)" :href="safeUrl(item.page_url)" target="_blank" rel="noopener noreferrer">{{ item.title }}</a><span v-else>{{ item.title }}</span></template>
              <template #item.quality_label="{ item }"><VChip size="small" variant="tonal" color="primary">{{ qualityLabel(item) }}</VChip></template>
              <template #item.bitrate_mbps="{ item }">{{ item.bitrate_mbps ? `${item.bitrate_mbps}M` : '未知' }}</template>
              <template #item.size_bytes="{ item }">{{ formatBytes(item.size_bytes) }}</template>
              <template #bottom><VPagination v-model="candidates.page" :length="Math.max(1, Math.ceil(candidates.total / 50))" @update:model-value="loadCandidates" /></template>
            </VDataTableServer>
          </div>
          <div class="mobile-list">
            <VCard v-for="item in candidates.items" :key="item.candidate_key" variant="outlined" class="mobile-card">
              <VCardText><VCheckbox v-model="selectedCandidates" :value="item.candidate_key" hide-details :label="`${item.year || '年份未知'} · ${item.site_name}`" /><a v-if="safeUrl(item.page_url)" :href="safeUrl(item.page_url)" target="_blank" rel="noopener noreferrer" class="mobile-title">{{ item.title }}</a><div class="chip-row"><VChip size="small">{{ qualityLabel(item) }}</VChip><VChip size="small" variant="text">{{ formatBytes(item.size_bytes) }}</VChip><VChip size="small" variant="text">{{ item.seeders }} 做种</VChip></div></VCardText>
            </VCard>
            <VPagination v-model="candidates.page" :length="Math.max(1, Math.ceil(candidates.total / 50))" @update:model-value="loadCandidates" />
          </div>
        </section>
      </VWindowItem>

      <VWindowItem value="jobs">
        <section aria-labelledby="jobs-title">
          <div class="section-heading"><div><h2 id="jobs-title">下载任务</h2><p>受控队列会把 reserved、queued、downloading 都计入版本上限。</p></div><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-refresh" @click="loadJobs">刷新任务</VBtn></div>
          <div class="desktop-table"><VDataTableServer :headers="jobHeaders" :items="jobs.items" :items-length="jobs.total" :items-per-page="50" :page="jobs.page" hover @update:page="value => { jobs.page=value; loadJobs() }">
            <template #item.status="{ item }"><VChip :color="statusColor(item.status)" size="small">{{ item.status }}</VChip></template>
            <template #item.automatic="{ item }">{{ item.automatic ? '自动' : '手动' }}</template>
            <template #item.result="{ item }"><span :class="item.error ? 'text-error' : ''">{{ item.error || item.download_id || '等待中' }}</span></template>
            <template #bottom><VPagination v-model="jobs.page" :length="Math.max(1, Math.ceil(jobs.total / 50))" @update:model-value="loadJobs" /></template>
          </VDataTableServer></div>
          <div class="mobile-list"><VCard v-for="item in jobs.items" :key="item.id" variant="outlined" class="mobile-card"><VCardText><div class="mobile-title">{{ item.title }}</div><div class="chip-row"><VChip :color="statusColor(item.status)" size="small">{{ item.status }}</VChip><VChip size="small" variant="text">{{ item.automatic ? '自动' : '手动' }}</VChip></div><small>{{ item.error || item.download_id || item.created_at }}</small></VCardText></VCard></div>
        </section>
      </VWindowItem>

      <VWindowItem value="settings">
        <section aria-labelledby="settings-title">
          <div class="section-heading"><div><h2 id="settings-title">规则与自动化</h2><p>自动下载默认关闭；保存后插件与定时任务会重新加载。</p></div><VBtn class="action-btn" color="primary" prepend-icon="mdi-content-save" :loading="loading" @click="saveSettings">保存设置</VBtn></div>
          <VForm @submit.prevent="saveSettings">
            <VCard variant="outlined" class="settings-card"><VCardTitle>基础与安全边界</VCardTitle><VCardText class="settings-grid">
              <VSwitch v-model="bootstrap.config.enabled" label="启用插件" color="primary" hide-details />
              <VSwitch v-model="bootstrap.config.auto_download" label="允许自动下载" color="primary" hide-details />
              <VSwitch v-model="bootstrap.config.pool_auto_download" label="允许全站种子池自动下载" color="primary" hide-details />
              <VSwitch v-model="bootstrap.config.allow_same_slot" label="允许相同质量槽位" color="primary" hide-details />
              <VSelect v-model="bootstrap.config.max_versions" label="每个影片/每集最多版本" :items="[1,2,3]" />
              <VTextField v-model.number="bootstrap.config.auto_batch_limit" type="number" min="1" max="50" label="每轮自动下载上限" />
              <VSelect v-model="bootstrap.config.sites" label="搜索站点（种子池仅使用 UBits）" :items="siteItems" item-title="name" item-value="id" multiple chips closable-chips />
              <VSelect v-model="bootstrap.config.emby_servers" label="Emby 服务" :items="serverItems" multiple chips closable-chips />
              <VTextField v-model="bootstrap.config.movie_save_path" label="电影下载保存路径" hint="留空使用 MoviePilot 默认目录；支持 storage:/path" persistent-hint />
              <VTextField v-model="bootstrap.config.tv_save_path" label="电视剧下载保存路径" hint="留空使用 MoviePilot 默认目录；支持 storage:/path" persistent-hint />
            </VCardText></VCard>

            <VCard variant="outlined" class="settings-card"><VCardTitle>质量保存路径</VCardTitle><VCardText>
              <p class="field-help">优先级：目标专用路径 &gt; 质量路径 &gt; 电影/电视剧默认路径。留空即继续使用后一级。</p>
              <div class="settings-grid">
                <VTextField v-for="item in qualityTypeItems" :key="item.value" v-model="bootstrap.config.quality_save_paths[item.value]" :label="`${item.title} 保存路径`" placeholder="storage:/path" />
              </div>
            </VCardText></VCard>

            <VCard variant="outlined" class="settings-card"><VCardTitle>Emby 媒体库</VCardTitle><VCardText><p class="field-help">每个服务留空表示同步全部媒体库。</p><div class="settings-grid"><VSelect v-for="server in bootstrap.options.emby_servers" :key="server.name" :model-value="bootstrap.config.emby_libraries?.[server.name] || []" :label="`${server.name} 媒体库`" :items="server.libraries" item-title="name" item-value="id" multiple chips closable-chips @update:model-value="value => setServerLibraries(server.name, value)" /></div></VCardText></VCard>

            <VCard variant="outlined" class="settings-card"><VCardTitle>质量筛选</VCardTitle><VCardText class="settings-grid">
              <VSelect v-model="bootstrap.config.quality_types" label="质量类型（选择顺序即优先级）" :items="qualityTypeItems" multiple chips closable-chips />
              <VSelect v-model="bootstrap.config.effects" label="动态范围（选择顺序即优先级）" :items="[{title:'Dolby Vision',value:'dv'},{title:'HDR10+',value:'hdr10plus'},{title:'HDR10',value:'hdr10'},{title:'HDR',value:'hdr'},{title:'HLG',value:'hlg'},{title:'SDR',value:'sdr'},{title:'未知',value:'unknown'}]" multiple chips closable-chips />
              <VSelect v-model="bootstrap.config.resolutions" label="分辨率（选择顺序即优先级）" :items="['2160p','1080p','720p','unknown']" multiple chips closable-chips />
              <VSelect v-model="bootstrap.config.video_codecs" label="视频编码（留空不限）" :items="['h265','h264','av1','unknown']" multiple chips closable-chips />
              <VTextField v-model.number="bootstrap.config.min_bitrate_mbps" type="number" min="0" label="最低码率 Mbps" />
              <VTextField v-model.number="bootstrap.config.max_bitrate_mbps" type="number" min="0" label="最高码率 Mbps（0 不限）" />
              <VSelect v-model="bootstrap.config.bitrate_order" label="同质量码率排序" :items="[{title:'高到低',value:'desc'},{title:'低到高',value:'asc'},{title:'不参与排序',value:'ignore'}]" />
              <VSwitch v-model="bootstrap.config.reject_unknown_bitrate" label="设置码率时拒绝未知码率" color="primary" hide-details />
              <VTextField v-model="bootstrap.config.include_words" label="必须包含关键词" hint="逗号分隔，全部命中才通过" persistent-hint />
              <VTextField v-model="bootstrap.config.exclude_words" label="排除关键词" hint="逗号分隔，任一命中即拒绝" persistent-hint />
              <div><VSwitch v-model="bootstrap.config.exclude_tv" label="排除所有剧集（仅保留电影）" color="primary" hide-details /><p class="field-help">默认开启；候选入池和下载提交前都会再次拦截剧集。</p></div>
            </VCardText></VCard>

            <VCard variant="outlined" class="settings-card"><VCardTitle>定时任务</VCardTitle><VCardText class="settings-grid">
              <VTextField v-model="bootstrap.config.inventory_cron" label="Emby 同步 Cron" />
              <VSwitch v-model="bootstrap.config.target_scan_enabled" label="启用目标定时搜索" color="primary" hide-details />
              <VTextField v-model="bootstrap.config.target_cron" label="目标搜索 Cron" />
              <VSwitch v-model="bootstrap.config.pool_scan_enabled" label="启用全站种子池定时刷新" color="primary" hide-details />
              <VTextField v-model="bootstrap.config.pool_cron" label="种子池刷新 Cron" />
            </VCardText></VCard>
            <div class="form-actions"><VBtn type="submit" class="action-btn" color="primary" prepend-icon="mdi-content-save">保存并应用</VBtn></div>
          </VForm>
        </section>
      </VWindowItem>
    </VWindow>

    <VDialog v-model="confirmDownload" max-width="560">
      <VCard><VCardTitle>确认批量下载</VCardTitle><VCardText>将处理当前选择的 {{ selectedCandidates.length }} 个候选。每个候选仍会执行媒体识别、同质量去重和最多三版本的原子校验，不符合条件的条目不会提交下载器。</VCardText><VCardActions><VSpacer /><VBtn class="action-btn" variant="text" @click="confirmDownload=false">取消</VBtn><VBtn class="action-btn" color="primary" @click="submitDownloads">确认下载</VBtn></VCardActions></VCard>
    </VDialog>

    <VDialog v-model="targetDialog" max-width="820" scrollable>
      <VCard><VCardTitle>{{ editingTargetId ? '编辑目标' : '新增目标' }}</VCardTitle><VCardText><VForm id="target-form" @submit.prevent="saveTarget"><div class="settings-grid">
        <VSelect v-model="targetForm.media_type" label="媒体类型" :items="[{title:'电影',value:'movie'},{title:'电视剧',value:'tv'}]" />
        <VTextField v-model="targetForm.title" label="标题" required />
        <VTextField v-model.number="targetForm.year" type="number" label="年份" />
        <VSelect v-model="targetForm.media_source" label="媒体数据源" :items="[{title:'TMDB',value:'themoviedb'},{title:'豆瓣',value:'douban'},{title:'Bangumi',value:'bangumi'},{title:'AniList',value:'anilist'}]" />
        <VTextField v-model="targetForm.media_id" label="媒体 ID（推荐填写）" />
        <VTextField v-if="targetForm.media_type === 'tv'" v-model="targetForm.seasons_text" label="季（逗号分隔）" placeholder="1,2" />
        <VSelect v-model="targetForm.desired_versions" label="目标版本数" :items="[1,2,3]" />
        <VSelect v-model="targetForm.sites" label="目标站点（留空使用全局）" :items="siteItems" item-title="name" item-value="id" multiple chips />
        <VTextField v-model="targetForm.save_path" label="专用保存路径（可选）" />
        <VSwitch v-model="targetForm.auto_download" label="允许此目标自动下载" color="primary" hide-details />
        <VSwitch v-model="targetForm.enabled" label="启用目标" color="primary" hide-details />
      </div></VForm></VCardText><VCardActions><VSpacer /><VBtn class="action-btn" variant="text" @click="targetDialog=false">取消</VBtn><VBtn class="action-btn" color="primary" type="submit" form="target-form">保存目标</VBtn></VCardActions></VCard>
    </VDialog>
  </main>
</template>

<style scoped>
.emby-app { --line: rgba(var(--v-border-color), var(--v-border-opacity)); max-width: 1680px; margin: 0 auto; color: rgb(var(--v-theme-on-surface)); }
.page-header, .section-heading, .selection-bar, .button-row, .filter-row { display: flex; align-items: center; gap: 12px; }
.page-header, .section-heading { justify-content: space-between; align-items: flex-start; }
h1 { font-size: clamp(1.7rem, 3vw, 2.6rem); line-height: 1.1; margin: 4px 0 10px; letter-spacing: -.03em; }
h2 { font-size: 1.35rem; margin: 0 0 4px; }
h3 { font-size: 1.1rem; margin: 3px 0 0; }
p { color: rgb(var(--v-theme-on-surface-variant)); margin: 0; }
.eyebrow { color: rgb(var(--v-theme-primary)); font-size: .72rem; font-weight: 800; letter-spacing: .16em; }
.action-btn { min-height: 44px; }
.section-tabs { border: 1px solid var(--line); border-radius: 14px; background: rgb(var(--v-theme-surface)); }
.stat-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; }
.stat-card :deep(.v-card-text) { display: grid; gap: 5px; }
.stat-card strong { font-size: 1.65rem; line-height: 1; }
.stat-card span { color: rgb(var(--v-theme-on-surface-variant)); font-size: .85rem; }
.content-card, .settings-card { border-radius: 16px; }
.workflow-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }
.workflow-grid > div { display: grid; gap: 7px; }
.workflow-grid span { color: rgb(var(--v-theme-on-surface-variant)); font-size: .86rem; }
.filter-row { display: grid; grid-template-columns: minmax(240px, 2fr) minmax(180px, 1fr) auto; margin: 18px 0; }
.desktop-table { border: 1px solid var(--line); border-radius: 16px; overflow: hidden; background: rgb(var(--v-theme-surface)); }
.path-cell { display: block; max-width: 440px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.target-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.target-card { border-radius: 16px; }
.target-top { display: flex; justify-content: space-between; gap: 12px; }
.target-top small { font-weight: 400; color: rgb(var(--v-theme-on-surface-variant)); }
dl { display: grid; gap: 8px; margin: 18px 0 0; }
dl div { display: flex; justify-content: space-between; gap: 18px; font-size: .86rem; }
dt { color: rgb(var(--v-theme-on-surface-variant)); }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.selection-bar { min-height: 56px; padding: 6px 12px; margin-bottom: 10px; border: 1px solid var(--line); border-radius: 14px; background: rgb(var(--v-theme-surface)); }
.settings-card { margin-bottom: 16px; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 16px; }
.field-help { margin-bottom: 16px; }
.form-actions { display: flex; justify-content: flex-end; padding: 4px 0 20px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }
.mobile-list { display: none; }
a { color: rgb(var(--v-theme-primary)); text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus-visible, button:focus-visible { outline: 3px solid rgb(var(--v-theme-primary)); outline-offset: 2px; }
@media (max-width: 1100px) { .stat-grid { grid-template-columns: repeat(3, 1fr); } .workflow-grid { grid-template-columns: repeat(2, 1fr); } .target-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 700px) {
  .page-header, .section-heading, .selection-bar { flex-direction: column; align-items: stretch; }
  .button-row { flex-wrap: wrap; }
  .button-row > * { flex: 1 1 auto; }
  .stat-grid, .target-grid, .settings-grid, .workflow-grid, .filter-row { grid-template-columns: 1fr; }
  .desktop-table { display: none; }
  .mobile-list { display: grid; gap: 10px; }
  .mobile-card { border-radius: 14px; overflow: hidden; }
  .mobile-title { display: block; color: rgb(var(--v-theme-on-surface)); font-weight: 700; overflow-wrap: anywhere; }
  .mobile-title span { color: rgb(var(--v-theme-on-surface-variant)); font-weight: 400; }
  .mobile-card p, .mobile-card small { display: block; margin-top: 10px; overflow-wrap: anywhere; }
}
</style>
