import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: 'EmbyLibraryDownload',
      filename: 'remoteEntry.js',
      exposes: {
        './AppPage': './src/AppPage.vue',
        './Page': './src/Page.vue',
        './Config': './src/Config.vue',
        './Dashboard': './src/Dashboard.vue',
      },
      shared: {
        vue: { requiredVersion: false, generate: false },
        vuetify: { requiredVersion: false, generate: false, singleton: true },
      },
      format: 'esm',
    }),
  ],
  build: {
    target: 'esnext',
    minify: true,
    cssCodeSplit: true,
    assetsDir: '',
    outDir: resolve(__dirname, '../../plugins.v2/embylibrarydownload/dist/assets'),
    emptyOutDir: true,
  },
})
