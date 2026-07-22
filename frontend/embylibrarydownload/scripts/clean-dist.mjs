import { readdirSync, rmSync } from 'node:fs'
import { resolve } from 'node:path'

const output = resolve(import.meta.dirname, '../../../plugins.v2/embylibrarydownload/dist/assets')
for (const name of readdirSync(output)) {
  if (name === 'index.html' || /^index-.*\.js$/.test(name)) rmSync(resolve(output, name))
}
