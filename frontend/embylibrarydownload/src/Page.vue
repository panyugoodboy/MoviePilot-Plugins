<script setup>
import AppPage from './AppPage.vue'
const props = defineProps({ api: { type: Object, default: () => ({}) } })
</script>
<template><AppPage :api="props.api" plugin-id="EmbyLibraryDownload" /></template>
