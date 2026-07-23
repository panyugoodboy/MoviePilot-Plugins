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
  config: {
    quality_save_paths: {}, exclude_tv: true, proxy_enabled: true,
    min_size_4k_gb: 0, min_size_1080p_gb: 0,
  },
  options: { sites: [], emby_servers: [] },
  stats: {},
  tasks: {},
  cron_previews: {},
})
const inventory = reactive({ items: [], total: 0, page: 1, keyword: '', media_type: '' })
const targets = ref([])
const candidates = reactive({
  items: [], total: 0, page: 1, keyword: '', site_id: null, scope: 'pool',
  quality_type: 'webdl', quality_counts: {},
})
const selectedCandidates = ref([])
const selectedJobs = ref([])
const jobs = reactive({ items: [], total: 0, failed_count: 0, page: 1 })
const confirmDownload = ref(false)
const targetDialog = ref(false)
const editingTargetId = ref(null)
const targetForm = reactive(emptyTarget())
const recommendationDialog = ref(false)
const recommendationLoading = ref(false)
const recommendationImporting = ref(false)
const notificationTesting = ref(false)
const recommendationSources = ref([])
const recommendation = reactive({ items: [], source: 'recommend/tmdb_movies', page: 1, canNext: false })
let pollTimer = null

const builtInRecommendationSources = [
  { title: 'TMDB 热门电影', value: 'recommend/tmdb_movies' },
  { title: '豆瓣热门电影', value: 'recommend/douban_movie_hot' },
  { title: '豆瓣正在热映', value: 'recommend/douban_showing' },
  { title: '豆瓣新片', value: 'recommend/douban_movies' },
  { title: '豆瓣电影 Top 250', value: 'recommend/douban_movie_top250' },
  { title: 'TMDB 流行趋势', value: 'recommend/tmdb_trending' },
]

const siteItems = computed(() => bootstrap.options.sites || [])
const serverItems = computed(() => (bootstrap.options.emby_servers || []).map(item => item.name))
const poolQualityItems = [
  { title: 'WEB-DL', value: 'webdl' },
  { title: 'Remux', value: 'remux' },
  { title: 'DIY 原盘', value: 'diy' },
  { title: 'Encode', value: 'encode' },
]
const candidateScopeLabel = computed(() => {
  if (candidates.scope === 'pool') {
    const category = poolQualityItems.find(item => item.value === candidates.quality_type)
    return `全站种子池 · ${category?.title || '分类'}`
  }
  const id = Number(candidates.scope.split(':')[1])
  const target = targets.value.find(item => item.id === id)
  return target ? `${target.title} 的候选资源` : '目标候选资源'
})
const hasRunningTask = computed(() => Object.values(bootstrap.tasks || {}).some(task => task.status === 'running'))
const poolTask = computed(() => bootstrap.tasks?.pool || null)
const targetTask = computed(() => bootstrap.tasks?.targets || null)
const targetPoolTask = computed(() => {
  const tasks = Object.entries(bootstrap.tasks || {})
    .filter(([name]) => name.startsWith('target-pool:'))
    .map(([name, task]) => ({ name, ...task }))
    .sort((left, right) => String(right.started_at || '').localeCompare(String(left.started_at || '')))
  return tasks[0] || null
})
const poolProgress = computed(() => poolTask.value?.progress || {})
const pageCandidateKeys = computed(() => candidates.items.map(item => item.candidate_key))
const allPageSelected = computed(() => pageCandidateKeys.value.length > 0 && pageCandidateKeys.value.every(key => selectedCandidates.value.includes(key)))
const pageJobIds = computed(() => jobs.items.map(item => item.id))
const allPageJobsSelected = computed(() => pageJobIds.value.length > 0 && pageJobIds.value.every(id => selectedJobs.value.includes(id)))
const selectedRetryableJobs = computed(() => jobs.items.filter(item => selectedJobs.value.includes(item.id) && ['failed', 'cancelled'].includes(item.status)))

const inventoryHeaders = [
  { title: '年份', key: 'year', width: 80 },
  { title: '媒体', key: 'title', minWidth: 220 },
  { title: '入库时间', key: 'date_created', width: 170, sortable: false },
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

async function callCoreGet(path, params) {
  try {
    const response = await props.api.get(String(path || '').replace(/^\//, ''), { params })
    if (response?.success === false) throw new Error(response.message || '推荐加载失败')
    return response?.data ?? response
  } catch (error) {
    const message = error?.response?.data?.message || error?.message || '推荐加载失败'
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
      quality_type: candidates.scope === 'pool' ? candidates.quality_type : undefined,
    })
    Object.assign(candidates, {
      items: data.items,
      total: data.total,
      quality_counts: data.quality_counts || {},
    })
  } finally {
    loading.value = false
  }
}

async function loadJobs() {
  selectedJobs.value = []
  const data = await call('get', '/jobs', null, { page: jobs.page, page_size: 50 })
  Object.assign(jobs, { items: data.items, total: data.total, failed_count: data.failed_count || 0 })
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

function toggleJobPageSelection() {
  selectedJobs.value = allPageJobsSelected.value ? [] : [...pageJobIds.value]
}

async function deleteSelectedJobs() {
  const ids = [...selectedJobs.value]
  if (!window.confirm(`确认删除所选 ${ids.length} 条任务记录？下载中的任务会自动跳过。`)) return
  const result = await call('post', '/jobs/delete', { job_ids: ids })
  toast?.success?.(`已删除 ${result.deleted} 条，跳过 ${result.blocked + result.missing} 条`)
  await loadJobs()
  await loadOverview()
}

async function retrySelectedJobs() {
  const ids = selectedRetryableJobs.value.map(item => item.id)
  await call('post', '/jobs/retry', { job_ids: ids })
  selectedJobs.value = []
  toast?.info?.(`已开始重试 ${ids.length} 个任务`)
  await loadOverview()
  startPolling()
}

async function retryAllFailedJobs() {
  if (!window.confirm(`确认重试全部 ${jobs.failed_count} 个失败任务？每个任务仍会重新执行库存、重复种子和版本上限校验。`)) return
  await call('post', '/jobs/retry-failed', {})
  selectedJobs.value = []
  toast?.info?.(`已开始重试全部 ${jobs.failed_count} 个失败任务`)
  await loadOverview()
  startPolling()
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
    media_type: 'movie', media_source: 'recommendation', media_id: '', title: '', original_title: '', year: null,
    poster_url: '', recommend_source: '', items: [], seasons_text: '', desired_versions: 3,
    sites: [], profile: {}, save_path: '',
    auto_download: true, prefer_scanned_pool: true, enabled: true,
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
  if (!editingTargetId.value && (!targetForm.recommend_source || !targetForm.items.length)) {
    const message = '请先从自定义推荐中选择一个完整榜单作为目标清单'
    actionError.value = message
    toast?.error?.(message)
    return
  }
  const payload = {
    ...targetForm,
    seasons: String(targetForm.seasons_text || '').split(',').map(value => Number(value.trim())).filter(Boolean),
  }
  delete payload.seasons_text
  const created = !editingTargetId.value
  const result = created
    ? await call('post', '/targets', payload)
    : await call('put', `/targets/${editingTargetId.value}`, payload)
  targetDialog.value = false
  toast?.success?.(created ? '目标已保存，正在匹配已扫描种子池' : '目标已保存')
  await loadTargets()
  if (result?.pool_task) {
    await loadOverview()
    startPolling()
  }
}

async function openRecommendationPicker() {
  recommendationDialog.value = true
  if (!recommendationSources.value.length) {
    recommendationSources.value = [...builtInRecommendationSources]
    try {
      const extra = await callCoreGet('recommend/source')
      for (const source of Array.isArray(extra) ? extra : []) {
        if (!source?.api_path || recommendationSources.value.some(item => item.value === source.api_path)) continue
        recommendationSources.value.push({ title: source.name, value: source.api_path })
      }
    } catch (_) {
      // 内置推荐仍然可用。
    }
  }
  recommendation.page = 1
  await loadRecommendations()
}

async function loadRecommendations() {
  recommendationLoading.value = true
  try {
    const data = await callCoreGet(recommendation.source, { page: recommendation.page, count: 30 })
    const items = Array.isArray(data) ? data : (data?.items || [])
    recommendation.items = bootstrap.config.exclude_tv
      ? items.filter(item => !isTvRecommendation(item))
      : items
    recommendation.canNext = items.length >= 20
  } finally {
    recommendationLoading.value = false
  }
}

async function changeRecommendationSource() {
  recommendation.page = 1
  await loadRecommendations()
}

async function changeRecommendationPage(offset) {
  recommendation.page = Math.max(1, recommendation.page + offset)
  await loadRecommendations()
}

function isTvRecommendation(item) {
  const type = String(item?.type || '').toLowerCase()
  return ['tv', '电视剧', '电视节目', 'anime', '动画'].some(value => type.includes(value))
}

function recommendationPoster(item) {
  const value = String(item?.poster_path || item?.backdrop_path || '').trim()
  if (value.startsWith('/')) return `https://image.tmdb.org/t/p/w500${value}`
  return safeUrl(value)
}

function normalizeRecommendationItem(item, position) {
  const source = String(item.source || (
    item.tmdb_id ? 'themoviedb' : item.douban_id ? 'douban' :
      item.bangumi_id ? 'bangumi' : item.anilist_id ? 'anilist' : item.imdb_id ? 'imdb' : 'themoviedb'
  )).toLowerCase()
  const ids = {
    themoviedb: item.tmdb_id,
    douban: item.douban_id,
    bangumi: item.bangumi_id,
    anilist: item.anilist_id,
    imdb: item.imdb_id,
    tvdb: item.tvdb_id,
  }
  return {
    media_type: isTvRecommendation(item) ? 'tv' : 'movie',
    media_source: source,
    media_id: String(ids[source] || item.media_id || ''),
    title: item.title || item.original_title || '',
    original_title: item.original_title || item.en_title || '',
    year: Number(item.year) || null,
    poster_url: recommendationPoster(item),
    position,
  }
}

function recommendationItemKey(item) {
  return `${item.media_source}:${item.media_id || `${String(item.title).toLowerCase()}:${item.year || ''}`}`
}

async function selectRecommendationSource() {
  recommendationImporting.value = true
  try {
    const imported = []
    const seen = new Set()
    let previousPageKey = ''
    for (let page = 1; page <= 100 && imported.length < 1000; page += 1) {
      const data = await callCoreGet(recommendation.source, { page, count: 30 })
      const rows = Array.isArray(data) ? data : (data?.items || [])
      const pageKey = rows.map(item => `${item.source}:${item.tmdb_id || item.douban_id || item.media_id || item.title}:${item.year || ''}`).join('|')
      if (!rows.length || pageKey === previousPageKey) break
      previousPageKey = pageKey
      const movies = bootstrap.config.exclude_tv ? rows.filter(item => !isTvRecommendation(item)) : rows
      for (const item of movies) {
        const normalized = normalizeRecommendationItem(item, imported.length)
        const key = recommendationItemKey(normalized)
        if (!normalized.title || seen.has(key)) continue
        seen.add(key)
        imported.push(normalized)
        if (imported.length >= 1000) break
      }
    }
    if (!imported.length) throw new Error('该推荐来源没有可导入的电影')
    const source = recommendationSources.value.find(item => item.value === recommendation.source)
    Object.assign(targetForm, {
      title: source?.title || '推荐目标清单',
      media_type: 'movie',
      media_source: 'recommendation',
      media_id: '',
      original_title: '',
      poster_url: '',
      recommend_source: recommendation.source,
      items: imported,
      auto_download: true,
      prefer_scanned_pool: true,
      enabled: true,
    })
    recommendationDialog.value = false
    toast?.success?.(`已导入 ${imported.length} 部影片作为目标清单`)
  } finally {
    recommendationImporting.value = false
  }
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

async function quickSearchTarget(target) {
  Object.assign(candidates, {
    scope: `target:${target.id}`, page: 1, keyword: '', site_id: null,
    items: [], total: 0, quality_counts: {},
  })
  tab.value = 'pool'
  await call('post', '/targets/search', { target_ids: [target.id] })
  toast?.info?.(`正在快速搜索“${target.title}”`)
  await loadOverview()
  startPolling()
}

async function showTargetCandidates(target) {
  candidates.scope = `target:${target.id}`
  candidates.page = 1
  tab.value = 'pool'
  await loadCandidates()
}

async function selectPoolQuality(value) {
  if (!value) return
  candidates.quality_type = value
  candidates.page = 1
  await loadCandidates()
}

async function saveSettings() {
  loading.value = true
  try {
    const response = await props.api.put(`plugin/${pluginId.value}`, bootstrap.config)
    if (response?.success === false) throw new Error(response.message || '保存失败')
    toast?.success?.('设置已保存并重新加载插件')
    await loadBootstrap(false)
    return true
  } catch (error) {
    const message = error?.response?.data?.message || error?.message || '保存失败'
    actionError.value = message
    toast?.error?.(message)
    return false
  } finally {
    loading.value = false
  }
}

async function testNotification() {
  notificationTesting.value = true
  try {
    await call('post', '/notifications/test', {})
    toast?.success?.('测试通知已发送，请检查 MoviePilot 通知渠道')
  } finally {
    notificationTesting.value = false
  }
}

async function runPoolOnce() {
  if (!await saveSettings()) return
  await runTask('/pool/refresh', '手动任务已开始')
}

function setServerLibraries(serverName, libraries) {
  if (!bootstrap.config.emby_libraries) bootstrap.config.emby_libraries = {}
  bootstrap.config.emby_libraries[serverName] = libraries
}

function cronPreview(key) {
  const preview = bootstrap.cron_previews?.[key]
  const expression = String(bootstrap.config?.[key] || '').trim()
  if (!preview || preview.expression !== expression) {
    return { valid: null, text: '保存后显示未来三次具体执行时间' }
  }
  return preview
}

function formatBytes(value) {
  const bytes = Number(value || 0)
  if (!bytes) return '未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 1 : 0)} ${units[index]}`
}

function formatDateTime(value) {
  if (!value) return '未知'
  const date = new Date(String(value).replace(/(\.\d{3})\d+/, '$1'))
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = number => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
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
  return { success: 'success', queued: 'info', downloading: 'primary', present: 'success', reserved: 'warning', running: 'primary', failed: 'error', cancelled: 'default' }[status] || 'default'
}

function statusLabel(status) {
  return {
    reserved: '已预留', queued: '已排队', downloading: '下载中', present: '已入库',
    failed: '失败', cancelled: '已取消', running: '运行中', success: '已完成',
  }[status] || status || '未知'
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
  <main class="emby-app pa-3 pa-md-4">
    <header class="page-header">
      <div class="page-brand">
        <div class="page-brand-icon" aria-hidden="true"><VIcon icon="mdi-movie-filter-outline" size="26" /></div>
        <div>
          <div class="eyebrow">EMBY LIBRARY CONTROL</div>
          <h1>联动 EMBY 库筛选下载</h1>
          <p>先核对已有版本，再按站点、质量和三版本上限补齐媒体库。</p>
        </div>
      </div>
      <VBtn class="action-btn" variant="tonal" prepend-icon="mdi-refresh" :loading="loading" @click="refreshCurrentTab">
        刷新当前页
      </VBtn>
    </header>

    <VAlert v-if="actionError" type="error" variant="tonal" closable class="mb-4" @click:close="actionError = ''">
      {{ actionError }}
    </VAlert>
    <VProgressLinear v-if="loading" indeterminate color="primary" class="mb-3" aria-label="正在加载" />

    <VTabs v-model="tab" class="section-tabs" show-arrows aria-label="插件功能导航">
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
                <template #append><VChip :color="statusColor(taskInfo.status)" size="small">{{ statusLabel(taskInfo.status) }}</VChip></template>
              </VListItem>
            </VList>
          </VCard>
        </section>
      </VWindowItem>

      <VWindowItem value="inventory">
        <section aria-labelledby="inventory-title">
          <div class="section-heading">
            <div><h2 id="inventory-title">EMBY 版本库存</h2><p>每个 MediaSource 记为一个版本，默认按 Emby 入库时间从新到旧排列。</p></div>
            <VBtn class="action-btn" color="primary" prepend-icon="mdi-database-sync" @click="runTask('/inventory/sync', 'Emby 同步已开始')">立即同步</VBtn>
          </div>
          <div class="filter-row">
            <VTextField v-model="inventory.keyword" label="搜索标题或路径" prepend-inner-icon="mdi-magnify" clearable hide-details @keyup.enter="loadInventory" />
            <VSelect v-model="inventory.media_type" label="媒体类型" :items="[{title:'全部',value:''},{title:'电影',value:'movie'},{title:'电视剧',value:'tv'}]" hide-details />
            <VBtn class="action-btn" variant="tonal" @click="loadInventory">筛选</VBtn>
          </div>
          <div class="desktop-table">
            <VDataTableServer :headers="inventoryHeaders" :items="inventory.items" :items-length="inventory.total" :items-per-page="50" :page="inventory.page" fixed-header hover @update:page="value => { inventory.page = value; loadInventory() }">
              <template #item.date_created="{ item }">{{ formatDateTime(item.date_created) }}</template>
              <template #item.episode_label="{ item }">{{ episodeLabel(item) }}</template>
              <template #item.quality_label="{ item }"><VChip size="small" variant="tonal">{{ qualityLabel(item) }}</VChip></template>
              <template #item.bitrate_mbps="{ item }">{{ item.bitrate_mbps ? `${item.bitrate_mbps} Mbps` : '未知' }}</template>
              <template #item.path="{ item }"><span class="path-cell" :title="item.path">{{ item.path }}</span></template>
              <template #bottom><VPagination v-model="inventory.page" :length="Math.max(1, Math.ceil(inventory.total / 50))" @update:model-value="loadInventory" /></template>
            </VDataTableServer>
          </div>
          <div class="mobile-list">
            <VCard v-for="item in inventory.items" :key="item.version_key" variant="outlined" class="mobile-card">
              <VCardText><div class="mobile-title">{{ item.title }} <span>{{ item.year || '年份未知' }}</span></div><VChip size="small">{{ episodeLabel(item) }}</VChip><p>{{ qualityLabel(item) }} · {{ item.bitrate_mbps || '未知' }} Mbps</p><small>入库时间：{{ formatDateTime(item.date_created) }}</small><small>{{ item.path }}</small></VCardText>
            </VCard>
            <VPagination v-model="inventory.page" :length="Math.max(1, Math.ceil(inventory.total / 50))" @update:model-value="loadInventory" />
          </div>
        </section>
      </VWindowItem>

      <VWindowItem value="targets">
        <section aria-labelledby="targets-title">
          <div class="section-heading">
            <div><h2 id="targets-title">目标清单</h2><p>一个目标对应一个完整推荐榜单；榜单内每部影片都是待补库项目。</p></div>
            <div class="button-row"><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-magnify" @click="searchTarget()">搜索全部</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-plus" @click="openTarget()">新增目标</VBtn></div>
          </div>
          <VAlert v-if="!targets.length" type="info" variant="tonal">暂无目标清单。可新增“豆瓣电影 Top 250”等推荐榜单，自动补齐其中全部影片。</VAlert>
          <VAlert v-if="targetPoolTask && ['running','success','failed'].includes(targetPoolTask.status)" :type="targetPoolTask.status === 'failed' ? 'error' : targetPoolTask.status === 'success' ? 'success' : 'info'" variant="tonal" class="mb-4">
            <div class="d-flex align-center ga-3">
              <VProgressCircular v-if="targetPoolTask.status === 'running'" indeterminate size="22" width="2" />
              <VIcon v-else :icon="targetPoolTask.status === 'success' ? 'mdi-check-circle' : 'mdi-alert-circle'" />
              <span>{{ targetPoolTask.status === 'running' ? '正在从已扫描种子池匹配新目标…' : targetPoolTask.result?.message || targetPoolTask.message }}</span>
            </div>
          </VAlert>
          <div class="target-grid">
            <VCard v-for="target in targets" :key="target.id" variant="outlined" class="target-card">
              <VCardText>
                <div class="target-list-header">
                  <div><span class="eyebrow">RECOMMENDATION LIST</span><h3>{{ target.title }}</h3><p>{{ target.item_count || target.items?.length || 1 }} 部影片 · 已入库 {{ target.in_library_count || 0 }} · 待补 {{ target.missing_count ?? target.item_count ?? 0 }}</p></div>
                  <div class="target-status"><VChip :color="target.inventory_state === 'present' ? 'success' : target.inventory_state === 'partial' ? 'info' : target.inventory_state === 'missing' ? 'warning' : 'default'" :prepend-icon="target.inventory_state === 'present' ? 'mdi-check-all' : 'mdi-progress-clock'">{{ target.inventory_state === 'present' ? '全部入库' : target.inventory_state === 'partial' ? '补库中' : target.inventory_state === 'missing' ? '等待入库' : '库存未同步' }}</VChip><VChip :color="target.enabled ? 'primary' : 'default'" variant="tonal">{{ target.enabled ? '启用' : '停用' }}</VChip></div>
                </div>
                <div class="button-row mt-4"><VBtn color="primary" variant="tonal" prepend-icon="mdi-database-search" @click="quickSearchTarget(target)">匹配已扫描种子</VBtn><VBtn variant="text" @click="showTargetCandidates(target)">查看候选</VBtn><VBtn variant="text" @click="openTarget(target)">编辑规则</VBtn><VBtn variant="text" color="error" @click="deleteTarget(target)">删除清单</VBtn></div>
                <VDivider class="my-4" />
                <div class="target-item-grid">
                  <article v-for="item in target.items || [target]" :key="`${item.media_source}:${item.media_id || item.title}`" class="target-item">
                    <div class="target-poster-wrap">
                      <VImg v-if="safeUrl(item.poster_url)" :src="safeUrl(item.poster_url)" :alt="`${item.title} 海报`" aspect-ratio="2/3" cover class="target-poster" loading="lazy" />
                      <div v-else class="target-poster target-poster-empty" role="img" :aria-label="`${item.title} 暂无海报`"><VIcon icon="mdi-movie-open-outline" size="42" /></div>
                      <div v-if="item.inventory_state === 'present'" class="library-check" aria-label="已在 Emby 媒体库"><VIcon icon="mdi-check-circle" /><span>已入库</span></div>
                    </div>
                    <div class="target-item-info"><strong>{{ item.title }}</strong><small>{{ item.year || '年份未知' }}</small></div>
                  </article>
                </div>
              </VCardText>
            </VCard>
          </div>
        </section>
      </VWindowItem>

      <VWindowItem value="pool">
        <section aria-labelledby="pool-title">
          <div class="section-heading">
            <div><h2 id="pool-title">{{ candidateScopeLabel }}</h2><p>{{ candidates.scope === 'pool' ? '按 UBits 的 WEB-DL、Remux、DIY 原盘、Encode 分类扫描全部页面；列表固定每页 50 条并按年份倒序。' : '这里展示目标快速搜索得到的站点候选；搜索结束后会自动刷新。' }}</p></div>
            <div class="button-row"><VBtn v-if="candidates.scope !== 'pool'" class="action-btn" variant="text" @click="candidates.scope='pool'; candidates.page=1; loadCandidates()">返回全站</VBtn><VBtn class="action-btn" color="primary" prepend-icon="mdi-radar" :loading="poolTask?.status === 'running'" @click="runTask('/pool/refresh', 'UBits 电影分类刷新已开始')">刷新 UBits 电影分类</VBtn></div>
          </div>
          <VAlert v-if="candidates.scope !== 'pool' && targetTask && ['running','failed'].includes(targetTask.status)" :type="targetTask.status === 'failed' ? 'error' : 'info'" variant="tonal" class="mb-4"><strong>{{ targetTask.status === 'running' ? '正在快速搜索目标站点资源…' : `搜索失败：${targetTask.message}` }}</strong><VProgressLinear v-if="targetTask.status === 'running'" indeterminate color="primary" class="mt-2" /></VAlert>
          <VAlert v-if="poolTask && (poolTask.status === 'running' || poolProgress.completed_pages)" :type="poolTask?.status === 'running' ? 'info' : poolTask?.status === 'failed' ? 'error' : 'success'" variant="tonal" class="mb-4">
            <div class="d-flex justify-space-between flex-wrap ga-2 mb-2">
              <strong>{{ poolTask?.status === 'running' ? poolTask.message : `上次刷新：${poolTask?.message || '已完成'}` }}</strong>
              <span>已扫描 {{ poolProgress.completed_pages || 0 }} 页 · 完成 {{ poolProgress.completed_sources || 0 }} / {{ poolProgress.total_sources || 4 }} 个分类</span>
            </div>
            <VProgressLinear :model-value="poolProgress.percent || 0" color="primary" height="8" rounded />
            <small>已发现 {{ poolProgress.found || 0 }} 个候选，其中 {{ poolProgress.eligible || 0 }} 个符合规则</small>
          </VAlert>
          <div v-if="candidates.scope === 'pool'" class="pool-quality-tabs">
            <VTabs :model-value="candidates.quality_type" show-arrows aria-label="种子池质量分类" @update:model-value="selectPoolQuality">
              <VTab v-for="item in poolQualityItems" :key="item.value" :value="item.value">
                <span class="pool-tab-label">{{ item.title }} <VChip size="x-small" variant="tonal">{{ candidates.quality_counts[item.value] || 0 }}</VChip></span>
              </VTab>
            </VTabs>
          </div>
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
          <div class="section-heading"><div><h2 id="jobs-title">下载任务</h2><p>已预留、已排队和下载中的任务都会计入版本上限；重复的旧失败记录会自动清理。</p></div><div class="button-row"><VBtn class="action-btn" color="warning" variant="tonal" prepend-icon="mdi-restart-alert" :disabled="!jobs.failed_count || hasRunningTask" @click="retryAllFailedJobs">全部重试失败任务（{{ jobs.failed_count }}）</VBtn><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-refresh" @click="loadJobs">刷新任务</VBtn></div></div>
          <div class="selection-bar">
            <VCheckbox :model-value="allPageJobsSelected" :indeterminate="selectedJobs.length > 0 && !allPageJobsSelected" hide-details label="当前页全选" @update:model-value="toggleJobPageSelection" />
            <span>已选 {{ selectedJobs.length }} / 当前页 {{ jobs.items.length }}</span>
            <VSpacer />
            <VBtn class="action-btn" color="error" variant="tonal" prepend-icon="mdi-delete-outline" :disabled="!selectedJobs.length || hasRunningTask" @click="deleteSelectedJobs">删除已选</VBtn>
            <VBtn class="action-btn" color="primary" prepend-icon="mdi-restart" :disabled="!selectedRetryableJobs.length || hasRunningTask" @click="retrySelectedJobs">重试已选（{{ selectedRetryableJobs.length }}）</VBtn>
          </div>
          <div class="desktop-table"><VDataTableServer v-model="selectedJobs" show-select item-value="id" :headers="jobHeaders" :items="jobs.items" :items-length="jobs.total" :items-per-page="50" :page="jobs.page" hover @update:page="value => { jobs.page=value; loadJobs() }">
            <template #item.status="{ item }"><VChip :color="statusColor(item.status)" size="small">{{ statusLabel(item.status) }}</VChip></template>
            <template #item.automatic="{ item }">{{ item.automatic ? '自动' : '手动' }}</template>
            <template #item.result="{ item }"><span :class="item.error ? 'text-error' : ''">{{ item.error || item.download_id || '等待中' }}</span></template>
            <template #bottom><VPagination v-model="jobs.page" :length="Math.max(1, Math.ceil(jobs.total / 50))" @update:model-value="loadJobs" /></template>
          </VDataTableServer></div>
          <div class="mobile-list"><VCard v-for="item in jobs.items" :key="item.id" variant="outlined" class="mobile-card"><VCardText><VCheckbox v-model="selectedJobs" :value="item.id" hide-details :label="item.created_at" /><div class="mobile-title">{{ item.title }}</div><div class="chip-row"><VChip :color="statusColor(item.status)" size="small">{{ statusLabel(item.status) }}</VChip><VChip size="small" variant="text">{{ item.automatic ? '自动' : '手动' }}</VChip></div><small>{{ item.error || item.download_id || item.created_at }}</small></VCardText></VCard></div>
        </section>
      </VWindowItem>

      <VWindowItem value="settings">
        <section aria-labelledby="settings-title">
          <div class="settings-hero">
            <div class="settings-hero-copy">
              <div class="settings-hero-icon" aria-hidden="true"><VIcon icon="mdi-tune-variant" size="30" /></div>
              <div><span class="eyebrow">CONTROL CENTER</span><h2 id="settings-title">规则与自动化</h2><p>按工作流分组管理库存、筛选、通知与定时任务，保存后插件会自动重新加载。</p></div>
            </div>
            <div class="settings-hero-actions">
              <VChip :color="bootstrap.config.enabled ? 'success' : 'default'" :prepend-icon="bootstrap.config.enabled ? 'mdi-check-circle-outline' : 'mdi-pause-circle-outline'" variant="tonal">{{ bootstrap.config.enabled ? '插件运行中' : '插件未启用' }}</VChip>
              <VChip :color="bootstrap.config.notify_enabled ? 'primary' : 'default'" prepend-icon="mdi-bell-outline" variant="tonal">{{ bootstrap.config.notify_enabled ? '汇总通知已开启' : '汇总通知已关闭' }}</VChip>
              <VBtn class="action-btn" color="primary" prepend-icon="mdi-content-save" :loading="loading" @click="saveSettings">保存设置</VBtn>
            </div>
          </div>
          <VForm @submit.prevent="saveSettings">
            <VCard variant="outlined" class="settings-card settings-card--primary">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-shield-check-outline" /></div><div><h3>基础与安全边界</h3><p>先确定自动化权限和版本上限，再配置站点、Emby 服务及保存位置。</p></div></div>
              <VCardText>
                <div class="settings-toggle-grid mb-5">
                  <div class="setting-toggle"><VIcon icon="mdi-power" /><VSwitch v-model="bootstrap.config.enabled" label="启用插件" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-download-circle-outline" /><VSwitch v-model="bootstrap.config.auto_download" label="允许自动下载" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-database-arrow-down-outline" /><VSwitch v-model="bootstrap.config.pool_auto_download" label="允许种子池自动下载" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-layers-triple-outline" /><VSwitch v-model="bootstrap.config.allow_same_slot" label="允许相同质量槽位" color="primary" hide-details /></div>
                  <div class="setting-toggle setting-toggle--featured"><VIcon icon="mdi-shield-lock-outline" /><VSwitch v-model="bootstrap.config.proxy_enabled" label="站点请求全程使用代理" color="primary" hide-details /></div>
                </div>
                <p class="field-help mb-5">开启后，站点搜索、全站分页、详情解析和种子文件下载统一使用 MoviePilot 系统代理；关闭后直连。Emby 局域网连接不走代理。</p>
                <div class="settings-grid">
                  <VSelect v-model="bootstrap.config.max_versions" label="每个影片/每集最多版本" :items="[1,2,3]" />
                  <VTextField v-model.number="bootstrap.config.auto_batch_limit" type="number" min="1" max="50" label="每次自动下载数量" hint="扫描完成和自动下载 Cron 均使用此数量；范围 1–50" persistent-hint />
                  <VSelect v-model="bootstrap.config.sites" label="搜索站点（种子池仅使用 UBits）" :items="siteItems" item-title="name" item-value="id" multiple chips closable-chips />
                  <VSelect v-model="bootstrap.config.emby_servers" label="Emby 服务" :items="serverItems" multiple chips closable-chips />
                  <VTextField v-model="bootstrap.config.movie_save_path" label="电影下载保存路径" hint="留空使用 MoviePilot 默认目录；支持 storage:/path" persistent-hint />
                  <VTextField v-model="bootstrap.config.tv_save_path" label="电视剧下载保存路径" hint="留空使用 MoviePilot 默认目录；支持 storage:/path" persistent-hint />
                </div>
              </VCardText>
            </VCard>

            <VCard variant="outlined" class="settings-card">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-folder-cog-outline" /></div><div><h3>质量保存路径</h3><p>为不同质量类型分配目录，未填写时自动回退到媒体类型默认路径。</p></div></div>
              <VCardText>
              <p class="field-help">路径优先级：目标专用路径 &gt; 质量路径 &gt; 电影/电视剧默认路径。</p>
              <div class="settings-grid">
                <VTextField v-for="item in qualityTypeItems" :key="item.value" v-model="bootstrap.config.quality_save_paths[item.value]" :label="`${item.title} 保存路径`" placeholder="storage:/path" />
              </div>
            </VCardText></VCard>

            <VCard variant="outlined" class="settings-card">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-server-network-outline" /></div><div><h3>Emby 媒体库</h3><p>限定需要建立本地库存的服务器和媒体库范围。</p></div></div>
              <VCardText><p class="field-help">每个服务留空表示同步该服务的全部媒体库。</p><div class="settings-grid"><VSelect v-for="server in bootstrap.options.emby_servers" :key="server.name" :model-value="bootstrap.config.emby_libraries?.[server.name] || []" :label="`${server.name} 媒体库`" :items="server.libraries" item-title="name" item-value="id" multiple chips closable-chips @update:model-value="value => setServerLibraries(server.name, value)" /></div></VCardText>
            </VCard>

            <VCard variant="outlined" class="settings-card">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-tune-vertical-variant" /></div><div><h3>质量筛选</h3><p>多选项的排列顺序就是自动选择优先级；最低体积按 GB 设置，0 表示不限制。</p></div></div>
              <VCardText>
                <div class="settings-grid">
                  <VSelect v-model="bootstrap.config.quality_types" label="质量类型（选择顺序即优先级）" :items="qualityTypeItems" multiple chips closable-chips />
                  <VSelect v-model="bootstrap.config.effects" label="动态范围（选择顺序即优先级）" :items="[{title:'Dolby Vision',value:'dv'},{title:'HDR10+',value:'hdr10plus'},{title:'HDR10',value:'hdr10'},{title:'HDR',value:'hdr'},{title:'HLG',value:'hlg'},{title:'SDR',value:'sdr'},{title:'未知',value:'unknown'}]" multiple chips closable-chips />
                  <VSelect v-model="bootstrap.config.resolutions" label="分辨率（选择顺序即优先级）" :items="['2160p','1080p','720p','unknown']" multiple chips closable-chips />
                  <VSelect v-model="bootstrap.config.video_codecs" label="视频编码（留空不限）" :items="['h265','h264','av1','unknown']" multiple chips closable-chips />
                  <VTextField v-model.number="bootstrap.config.min_size_4k_gb" type="number" min="0" step="0.1" label="4K 最低体积 GB（0 不限）" />
                  <VTextField v-model.number="bootstrap.config.min_size_1080p_gb" type="number" min="0" step="0.1" label="1080P 最低体积 GB（0 不限）" />
                  <VTextField v-model.number="bootstrap.config.min_bitrate_mbps" type="number" min="0" label="最低码率 Mbps" />
                  <VTextField v-model.number="bootstrap.config.max_bitrate_mbps" type="number" min="0" label="最高码率 Mbps（0 不限）" />
                  <VSelect v-model="bootstrap.config.bitrate_order" label="同质量码率排序" :items="[{title:'高到低',value:'desc'},{title:'低到高',value:'asc'},{title:'不参与排序',value:'ignore'}]" />
                  <VTextField v-model="bootstrap.config.include_words" label="必须包含关键词" hint="逗号分隔，全部命中才通过" persistent-hint />
                  <VTextField v-model="bootstrap.config.exclude_words" label="排除关键词" hint="逗号分隔，任一命中即拒绝" persistent-hint />
                </div>
                <div class="settings-toggle-grid mt-2">
                  <div class="setting-toggle"><VIcon icon="mdi-speedometer-slow" /><VSwitch v-model="bootstrap.config.reject_unknown_bitrate" label="拒绝未知码率" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-television-off" /><VSwitch v-model="bootstrap.config.exclude_tv" label="排除所有剧集" color="primary" hide-details /></div>
                </div>
              </VCardText>
            </VCard>

            <VCard variant="outlined" class="settings-card settings-card--notification">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-bell-badge-outline" /></div><div><h3>汇总通知</h3><p>默认开启，通过 MoviePilot 通知渠道发送美化后的纯数据汇总。</p></div></div>
              <VCardText>
                <VAlert type="info" variant="tonal" icon="mdi-information-outline" class="mb-4">通知只显示统计数字，不包含影片名、种子名、路径、Cookie 或报错详情。</VAlert>
                <div class="settings-toggle-grid notification-grid">
                  <div class="setting-toggle setting-toggle--featured"><VIcon icon="mdi-bell-ring-outline" /><VSwitch v-model="bootstrap.config.notify_enabled" label="开启汇总通知" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-server-outline" /><VSwitch v-model="bootstrap.config.notify_inventory" label="库存同步汇总" color="primary" :disabled="!bootstrap.config.notify_enabled" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-playlist-check" /><VSwitch v-model="bootstrap.config.notify_targets" label="目标清单汇总" color="primary" :disabled="!bootstrap.config.notify_enabled" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-database-search-outline" /><VSwitch v-model="bootstrap.config.notify_pool" label="种子池扫描汇总" color="primary" :disabled="!bootstrap.config.notify_enabled" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-download-box-outline" /><VSwitch v-model="bootstrap.config.notify_download" label="下载任务汇总" color="primary" :disabled="!bootstrap.config.notify_enabled" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-alert-circle-outline" /><VSwitch v-model="bootstrap.config.notify_failures" label="任务异常汇总" color="primary" :disabled="!bootstrap.config.notify_enabled" hide-details /></div>
                </div>
                <div class="notification-actions"><div><strong>测试通知链路</strong><p class="field-help">发送一条不含业务详情的统计测试消息。</p></div><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-send-check-outline" :loading="notificationTesting" @click="testNotification">发送测试通知</VBtn></div>
              </VCardText>
            </VCard>

            <VCard variant="outlined" class="settings-card">
              <div class="settings-card-header"><div class="settings-card-icon"><VIcon icon="mdi-calendar-clock-outline" /></div><div><h3>定时任务</h3><p>每个 Cron 都显示可读时间，开关关闭时不会注册对应任务。</p></div></div>
              <VCardText>
                <div class="run-once-panel mb-5"><div><strong>立即运行一轮种子池任务</strong><p class="field-help">保存当前设置后扫描 UBits 四类全部分页；自动下载开启时最多提交 {{ bootstrap.config.auto_batch_limit }} 个候选。</p></div><VBtn class="action-btn" variant="tonal" prepend-icon="mdi-play-circle-outline" :loading="loading || poolTask?.status === 'running'" :disabled="hasRunningTask || loading" @click="runPoolOnce">手动执行一次</VBtn></div>
                <div class="settings-grid">
                  <VTextField v-model="bootstrap.config.inventory_cron" label="Emby 同步 Cron" :hint="cronPreview('inventory_cron').text" :error="cronPreview('inventory_cron').valid === false" persistent-hint />
                  <VTextField v-model="bootstrap.config.target_cron" label="目标搜索 Cron" :hint="cronPreview('target_cron').text" :error="cronPreview('target_cron').valid === false" :disabled="!bootstrap.config.target_scan_enabled" persistent-hint />
                  <VTextField v-model="bootstrap.config.pool_cron" label="种子池刷新 Cron" :hint="cronPreview('pool_cron').text" :error="cronPreview('pool_cron').valid === false" :disabled="!bootstrap.config.pool_scan_enabled" persistent-hint />
                  <VTextField v-model="bootstrap.config.auto_download_cron" label="自动下载 Cron（可选）" :hint="cronPreview('auto_download_cron').text" :error="cronPreview('auto_download_cron').valid === false" :disabled="!bootstrap.config.auto_download || !bootstrap.config.pool_auto_download" persistent-hint />
                </div>
                <div class="settings-toggle-grid mt-2">
                  <div class="setting-toggle"><VIcon icon="mdi-target" /><VSwitch v-model="bootstrap.config.target_scan_enabled" label="启用目标定时搜索" color="primary" hide-details /></div>
                  <div class="setting-toggle"><VIcon icon="mdi-database-sync-outline" /><VSwitch v-model="bootstrap.config.pool_scan_enabled" label="启用种子池定时刷新" color="primary" hide-details /></div>
                </div>
              </VCardText>
            </VCard>
            <div class="form-actions"><VBtn type="submit" class="action-btn" color="primary" prepend-icon="mdi-content-save">保存并应用</VBtn></div>
          </VForm>
        </section>
      </VWindowItem>
    </VWindow>

    <VDialog v-model="confirmDownload" max-width="560">
      <VCard><VCardTitle>确认批量下载</VCardTitle><VCardText>将处理当前选择的 {{ selectedCandidates.length }} 个候选。每个候选仍会执行媒体识别、同质量去重和最多三版本的原子校验，不符合条件的条目不会提交下载器。</VCardText><VCardActions><VSpacer /><VBtn class="action-btn" variant="text" @click="confirmDownload=false">取消</VBtn><VBtn class="action-btn" color="primary" @click="submitDownloads">确认下载</VBtn></VCardActions></VCard>
    </VDialog>

    <VDialog v-model="recommendationDialog" max-width="1180" scrollable>
      <VCard>
        <VCardTitle class="dialog-title"><div><span>选择目标榜单</span><small>整个推荐来源会作为一个目标清单，不是只添加单部影片</small></div><VBtn icon="mdi-close" variant="text" aria-label="关闭推荐选择" @click="recommendationDialog=false" /></VCardTitle>
        <VCardText>
          <div class="recommend-toolbar">
            <VSelect v-model="recommendation.source" :items="recommendationSources" label="推荐来源" hide-details @update:model-value="changeRecommendationSource" />
            <VBtn class="action-btn" color="primary" prepend-icon="mdi-playlist-plus" :loading="recommendationImporting" :disabled="recommendationLoading" @click="selectRecommendationSource">将整个榜单设为目标</VBtn>
          </div>
          <div class="recommend-pager mt-3"><VBtn variant="tonal" prepend-icon="mdi-chevron-left" :disabled="recommendation.page <= 1 || recommendationLoading" @click="changeRecommendationPage(-1)">上一页</VBtn><span>预览第 {{ recommendation.page }} 页</span><VBtn variant="tonal" append-icon="mdi-chevron-right" :disabled="!recommendation.canNext || recommendationLoading" @click="changeRecommendationPage(1)">下一页</VBtn></div>
          <VAlert v-if="recommendationImporting" type="info" variant="tonal" class="mt-4">正在遍历该推荐来源的全部分页并导入影片，固定榜单会一直读取到最后一页，最多导入 1000 部。</VAlert>
          <VProgressLinear v-if="recommendationLoading" indeterminate color="primary" class="my-4" aria-label="正在加载推荐" />
          <VAlert v-else-if="!recommendation.items.length" type="info" variant="tonal" class="mt-4">当前来源没有可用的电影推荐。</VAlert>
          <div v-else class="recommend-grid mt-4">
            <VCard v-for="item in recommendation.items" :key="`${item.source}:${item.tmdb_id || item.douban_id || item.media_id || item.title}`" variant="outlined" class="recommend-card">
              <VImg v-if="recommendationPoster(item)" :src="recommendationPoster(item)" :alt="`${item.title} 海报`" aspect-ratio="2/3" cover class="recommend-poster" loading="lazy" />
              <div v-else class="recommend-poster target-poster-empty"><VIcon icon="mdi-movie-open-outline" size="42" /></div>
              <VCardText><strong>{{ item.title }}</strong><small>{{ item.year || '年份未知' }} · {{ item.source || '推荐' }}</small></VCardText>
            </VCard>
          </div>
        </VCardText>
        <VCardActions><VSpacer /><VBtn variant="text" @click="recommendationDialog=false">取消</VBtn></VCardActions>
      </VCard>
    </VDialog>

    <VDialog v-model="targetDialog" max-width="820" scrollable>
      <VCard><VCardTitle>{{ editingTargetId ? '编辑目标' : '新增目标' }}</VCardTitle><VCardText><VForm id="target-form" @submit.prevent="saveTarget"><div class="settings-grid">
        <div class="recommend-entry"><VBtn class="action-btn" color="primary" variant="tonal" prepend-icon="mdi-playlist-plus" @click="openRecommendationPicker">选择推荐榜单</VBtn><span>{{ targetForm.items.length ? `已选择“${targetForm.title}”，共 ${targetForm.items.length} 部影片` : '例如选择“豆瓣电影 Top 250”，会把榜单内全部影片加入此目标清单。' }}</span></div>
        <VTextField v-model="targetForm.title" label="目标清单名称" required />
        <VTextField :model-value="targetForm.recommend_source || '尚未选择'" label="推荐来源" readonly />
        <VTextField :model-value="targetForm.items.length" label="榜单影片数量" readonly />
        <VSelect v-model="targetForm.desired_versions" label="榜单内每部影片目标版本数" :items="[1,2,3]" />
        <VSelect v-model="targetForm.sites" label="目标站点（留空使用全局）" :items="siteItems" item-title="name" item-value="id" multiple chips />
        <VTextField v-model="targetForm.save_path" label="专用保存路径（可选）" />
        <VSwitch v-model="targetForm.auto_download" label="允许此目标自动下载" color="primary" hide-details />
        <div><VSwitch v-model="targetForm.prefer_scanned_pool" label="优先下载榜单在已扫描种子池中的匹配项" color="primary" :disabled="!targetForm.auto_download" hide-details /><p class="field-help">按榜单内每部影片的媒体 ID、标题、年份及质量规则匹配；每轮仍受自动下载数量限制。</p></div>
        <VSwitch v-model="targetForm.enabled" label="启用目标" color="primary" hide-details />
      </div></VForm></VCardText><VCardActions><VSpacer /><VBtn class="action-btn" variant="text" @click="targetDialog=false">取消</VBtn><VBtn class="action-btn" color="primary" type="submit" form="target-form">保存目标</VBtn></VCardActions></VCard>
    </VDialog>
  </main>
</template>

<style scoped>
.emby-app {
  --line: rgba(var(--v-border-color), calc(var(--v-border-opacity) + .08));
  --line-strong: rgba(var(--v-theme-primary), .3);
  --surface-soft: rgba(var(--v-theme-surface-variant), .22);
  --text-muted: rgba(var(--v-theme-on-surface), .68);
  max-width: 1600px;
  margin: 0 auto;
  color: rgb(var(--v-theme-on-surface));
  font-variant-numeric: tabular-nums;
}

.page-header,
.page-brand,
.section-heading,
.selection-bar,
.button-row,
.settings-hero-copy,
.settings-hero-actions,
.settings-card-header,
.setting-toggle,
.notification-actions,
.run-once-panel {
  display: flex;
  align-items: center;
}

.page-header {
  justify-content: space-between;
  gap: 20px;
  padding: 16px 18px;
  margin-bottom: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: linear-gradient(110deg, rgba(var(--v-theme-primary), .11), rgb(var(--v-theme-surface)) 42%);
  box-shadow: 0 8px 24px rgba(var(--v-theme-on-surface), .045);
}

.page-brand { min-width: 0; gap: 14px; }
.page-brand-icon,
.settings-hero-icon,
.settings-card-icon {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), .12);
  border: 1px solid rgba(var(--v-theme-primary), .16);
}
.page-brand-icon { width: 46px; height: 46px; border-radius: 12px; }

h1 { margin: 2px 0 3px; font-size: clamp(1.45rem, 2.4vw, 2rem); line-height: 1.15; letter-spacing: -.025em; }
h2 { margin: 0 0 3px; font-size: 1.2rem; line-height: 1.3; letter-spacing: -.012em; }
h3 { margin: 2px 0 0; font-size: 1.02rem; line-height: 1.35; }
p { margin: 0; color: var(--text-muted); line-height: 1.5; }
.eyebrow { color: rgb(var(--v-theme-primary)); font-size: .67rem; font-weight: 800; letter-spacing: .13em; }

.action-btn { min-height: 44px; border-radius: 10px; }
.emby-app :deep(.v-btn) { min-height: 44px; letter-spacing: .01em; }
.emby-app :deep(.v-field) { border-radius: 10px; }
.emby-app :deep(.v-chip) { border-radius: 8px; }

.section-tabs {
  min-height: 52px;
  margin-bottom: 16px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgb(var(--v-theme-surface));
  box-shadow: 0 4px 14px rgba(var(--v-theme-on-surface), .025);
}
.section-tabs :deep(.v-tab) { min-height: 44px; border-radius: 9px; text-transform: none; font-weight: 650; }
.section-tabs :deep(.v-tab.v-tab--selected) { background: rgba(var(--v-theme-primary), .11); }

.section-heading {
  justify-content: space-between;
  align-items: flex-start;
  gap: 18px;
  min-height: 44px;
  margin-bottom: 14px;
}
.section-heading > div:first-child { min-width: 0; }
.section-heading p { max-width: 860px; font-size: .88rem; }
.button-row { flex-wrap: wrap; gap: 8px; }

.stat-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }
.stat-card,
.content-card,
.target-card,
.recommend-card,
.mobile-card,
.settings-card {
  overflow: hidden;
  border-color: var(--line);
  border-radius: 12px;
  background: rgb(var(--v-theme-surface));
  transition: border-color 180ms ease, box-shadow 180ms ease;
}
.stat-card:hover,
.target-card:hover,
.recommend-card:hover { border-color: var(--line-strong); box-shadow: 0 8px 24px rgba(var(--v-theme-on-surface), .055); }
.stat-card :deep(.v-card-text) {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  grid-template-rows: auto auto;
  align-items: center;
  column-gap: 10px;
  padding: 14px;
}
.stat-card :deep(.v-icon) { grid-row: 1 / 3; padding: 8px; border-radius: 10px; background: rgba(var(--v-theme-primary), .1); }
.stat-card strong { align-self: end; font-size: 1.42rem; line-height: 1.1; }
.stat-card span { align-self: start; color: var(--text-muted); font-size: .79rem; }

.content-card :deep(.v-card-title) { padding: 13px 16px 8px; font-size: 1rem; font-weight: 700; }
.content-card :deep(.v-card-text) { padding: 12px 16px 16px; }
.workflow-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; }
.workflow-grid > div { display: grid; grid-template-columns: 24px 1fr; gap: 4px 8px; min-width: 0; padding: 4px 16px; border-left: 1px solid var(--line); }
.workflow-grid > div:first-child { padding-left: 0; border-left: 0; }
.workflow-grid > div:last-child { padding-right: 0; }
.workflow-grid .v-icon { grid-row: 1 / 3; margin-top: 1px; color: rgb(var(--v-theme-primary)); }
.workflow-grid b { font-size: .88rem; }
.workflow-grid span { color: var(--text-muted); font-size: .79rem; line-height: 1.45; }

.filter-row { display: grid; grid-template-columns: minmax(240px, 2fr) minmax(180px, 1fr) auto; align-items: center; gap: 10px; margin: 12px 0; }
.pool-quality-tabs { margin: 12px 0 0; padding: 3px; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: rgb(var(--v-theme-surface)); }
.pool-quality-tabs :deep(.v-tab) { min-height: 44px; border-radius: 9px; text-transform: none; }
.pool-quality-tabs :deep(.v-tab.v-tab--selected) { background: rgba(var(--v-theme-primary), .1); }
.pool-tab-label { display: inline-flex; align-items: center; gap: 7px; }

.selection-bar { gap: 10px; min-height: 50px; padding: 3px 10px; margin-bottom: 10px; border: 1px solid var(--line); border-radius: 11px; background: var(--surface-soft); }
.selection-bar > span { color: var(--text-muted); font-size: .84rem; }
.desktop-table { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: rgb(var(--v-theme-surface)); }
.desktop-table :deep(.v-data-table-header__content) { font-size: .78rem; font-weight: 750; letter-spacing: .025em; }
.desktop-table :deep(thead) { background: var(--surface-soft); }
.desktop-table :deep(.v-data-table__tr) { height: 48px; }
.desktop-table :deep(.v-data-table__td),
.desktop-table :deep(.v-data-table__th) { padding-inline: 12px; }
.desktop-table :deep(.v-pagination) { padding: 8px; }
.path-cell { display: block; max-width: 440px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.target-grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.target-card :deep(.v-card-text) { padding: 16px; }
.target-list-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 14px; }
.target-list-header h3 { font-size: 1.12rem; }
.target-list-header p { margin-top: 3px; font-size: .86rem; }
.target-status { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.target-card .button-row :deep(.v-btn) { min-height: 44px; }
.target-item-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 10px; }
.target-item { min-width: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: rgb(var(--v-theme-surface)); }
.target-item-info { display: grid; gap: 2px; padding: 8px; }
.target-item-info strong { font-size: .85rem; line-height: 1.35; overflow-wrap: anywhere; }
.target-item-info small { color: var(--text-muted); font-size: .75rem; }
.target-poster-wrap { position: relative; aspect-ratio: 2 / 3; overflow: hidden; background: rgb(var(--v-theme-surface-variant)); }
.target-poster { width: 100%; height: 100%; }
.target-poster-empty { display: grid; place-content: center; justify-items: center; gap: 8px; color: var(--text-muted); background: linear-gradient(145deg, rgb(var(--v-theme-surface-variant)), rgb(var(--v-theme-surface))); }
.library-check { position: absolute; top: 8px; right: 8px; display: inline-flex; align-items: center; gap: 4px; min-height: 32px; padding: 5px 8px; border-radius: 999px; color: rgb(var(--v-theme-on-success)); background: rgb(var(--v-theme-success)); font-size: .74rem; font-weight: 800; box-shadow: 0 5px 14px rgba(var(--v-theme-on-surface), .22); }

.dialog-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.dialog-title > div { display: grid; gap: 2px; }
.dialog-title small { color: var(--text-muted); font-size: .78rem; font-weight: 400; white-space: normal; }
.recommend-toolbar { display: grid; grid-template-columns: minmax(240px, 1fr) auto; align-items: center; gap: 12px; }
.recommend-pager { display: flex; align-items: center; gap: 10px; }
.recommend-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(135px, 1fr)); gap: 10px; }
.recommend-poster { width: 100%; aspect-ratio: 2 / 3; }
.recommend-card :deep(.v-card-text) { display: grid; gap: 5px; padding: 10px; }
.recommend-card strong { min-height: 2.7em; font-size: .88rem; line-height: 1.35; }
.recommend-card small { color: var(--text-muted); font-size: .76rem; }
.recommend-entry { grid-column: 1 / -1; display: flex; align-items: center; gap: 10px; padding: 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
.recommend-entry span { color: var(--text-muted); font-size: .82rem; }

.settings-hero { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 14px 16px; margin-bottom: 12px; border: 1px solid var(--line-strong); border-radius: 12px; background: linear-gradient(120deg, rgba(var(--v-theme-primary), .09), rgb(var(--v-theme-surface)) 56%); }
.settings-hero-copy { gap: 12px; min-width: 0; }
.settings-hero-copy h2 { margin-top: 2px; }
.settings-hero-copy p { max-width: 720px; font-size: .84rem; }
.settings-hero-icon { width: 44px; height: 44px; border-radius: 11px; }
.settings-hero-actions { justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.settings-card { margin-bottom: 12px; }
.settings-card--primary { border-color: var(--line-strong); }
.settings-card--notification { border-color: rgba(var(--v-theme-info), .34); background: linear-gradient(150deg, rgba(var(--v-theme-info), .05), rgb(var(--v-theme-surface)) 42%); }
.settings-card-header { align-items: flex-start; gap: 11px; padding: 14px 16px 0; }
.settings-card-header h3 { margin: 0 0 2px; font-size: .98rem; }
.settings-card-header p { max-width: 760px; font-size: .8rem; line-height: 1.45; }
.settings-card-icon { width: 38px; height: 38px; border-radius: 10px; }
.settings-card :deep(.v-card-text) { padding: 14px 16px 16px; }
.settings-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 12px; }
.settings-toggle-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.setting-toggle { gap: 8px; min-height: 52px; padding: 1px 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); }
.setting-toggle > .v-icon { flex: 0 0 auto; color: rgb(var(--v-theme-primary)); }
.setting-toggle :deep(.v-switch) { flex: 1; min-width: 0; }
.setting-toggle :deep(.v-selection-control) { min-height: 48px; }
.setting-toggle--featured { grid-column: 1 / -1; border-color: var(--line-strong); background: rgba(var(--v-theme-primary), .075); }
.notification-actions,
.run-once-panel { justify-content: space-between; gap: 14px; padding: 12px; margin-top: 12px; border: 1px dashed var(--line-strong); border-radius: 10px; background: rgba(var(--v-theme-primary), .04); }
.notification-actions .field-help,
.run-once-panel .field-help { margin: 2px 0 0; }
.field-help { margin-bottom: 12px; color: var(--text-muted); font-size: .8rem; line-height: 1.45; }
.form-actions { display: flex; justify-content: flex-end; padding: 2px 0 12px; }
.chip-row { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.mobile-list { display: none; }

a { color: rgb(var(--v-theme-primary)); text-decoration: none; text-underline-offset: 3px; }
a:hover { text-decoration: underline; }
a:focus-visible,
button:focus-visible { outline: 3px solid rgba(var(--v-theme-primary), .72); outline-offset: 2px; }

@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
  .workflow-grid { grid-template-columns: repeat(2, 1fr); gap: 14px 0; }
  .workflow-grid > div:nth-child(3) { padding-left: 0; border-left: 0; }
  .settings-hero { align-items: flex-start; flex-direction: column; }
  .settings-hero-actions { justify-content: flex-start; }
}

@media (max-width: 700px) {
  .emby-app { padding-inline: 12px !important; }
  .page-header,
  .section-heading,
  .selection-bar { flex-direction: column; align-items: stretch; }
  .page-header { gap: 14px; padding: 14px; }
  .page-brand { align-items: flex-start; }
  .page-brand-icon { width: 42px; height: 42px; }
  h1 { font-size: 1.42rem; }
  .page-header > .action-btn { width: 100%; }
  .section-tabs { margin-inline: -2px; }
  .section-heading { gap: 10px; margin-bottom: 12px; }
  .button-row { flex-wrap: wrap; }
  .button-row > * { flex: 1 1 auto; }
  .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .target-grid,
  .settings-grid,
  .settings-toggle-grid,
  .filter-row,
  .recommend-toolbar { grid-template-columns: 1fr; }
  .workflow-grid { grid-template-columns: 1fr; gap: 0; }
  .workflow-grid > div,
  .workflow-grid > div:nth-child(3) { padding: 10px 0; border-top: 1px solid var(--line); border-left: 0; }
  .workflow-grid > div:first-child { padding-top: 0; border-top: 0; }
  .workflow-grid > div:last-child { padding-bottom: 0; }
  .settings-hero { padding: 13px; }
  .settings-hero-copy { align-items: flex-start; }
  .settings-hero-actions { align-items: stretch; flex-direction: column; width: 100%; }
  .settings-hero-actions :deep(.v-btn) { width: 100%; }
  .settings-card-header { padding: 13px 13px 0; }
  .settings-card :deep(.v-card-text) { padding: 13px; }
  .setting-toggle--featured { grid-column: auto; }
  .notification-actions,
  .run-once-panel { align-items: stretch; flex-direction: column; }
  .recommend-grid,
  .target-item-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .target-list-header { flex-direction: column; }
  .target-status { justify-content: flex-start; }
  .recommend-pager { justify-content: space-between; }
  .recommend-pager :deep(.v-btn) { min-width: 44px; }
  .recommend-entry { align-items: stretch; flex-direction: column; }
  .selection-bar { padding: 8px 10px; }
  .desktop-table { display: none; }
  .mobile-list { display: grid; gap: 8px; }
  .mobile-card :deep(.v-card-text) { padding: 12px; }
  .mobile-title { display: block; color: rgb(var(--v-theme-on-surface)); font-weight: 700; overflow-wrap: anywhere; }
  .mobile-title span { color: var(--text-muted); font-weight: 400; }
  .mobile-card p,
  .mobile-card small { display: block; margin-top: 7px; overflow-wrap: anywhere; }
}

@media (max-width: 390px) {
  .page-brand-icon { display: none; }
}

@media (prefers-reduced-motion: reduce) {
  .stat-card,
  .target-card,
  .recommend-card { transition: none; }
}
</style>
