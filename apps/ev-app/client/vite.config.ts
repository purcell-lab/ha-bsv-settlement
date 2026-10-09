import { readdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

// The integration serves this build as a static path at /bsv_settlement/app/
// (custom_components/bsv_settlement/__init__.py), next to the driver page.
// Output is committed and must be reproducible: fixed file names, one chunk,
// no source maps, and nothing derived from the machine or checkout path.
const outDir = fileURLToPath(new URL('../../../custom_components/bsv_settlement/frontend/app', import.meta.url))
const modules = fileURLToPath(new URL('./node_modules/', import.meta.url))

// Licence texts that must travel with the browser bundle, in a fixed order.
// @bsv/sdk asks distributors to keep THIRD_PARTY_NOTICES.md and LICENSES/.
function licences (): Plugin {
  // Line endings and trailing blanks normalised so the output is stable and diff-clean.
  const read = (path: string) => readFileSync(modules + path, 'utf8').replace(/\r\n/g, '\n').replace(/[ \t]+$/gm, '').trimEnd()
  return {
    name: 'ev-app-licences',
    apply: 'build',
    generateBundle () {
      const sdkLicences = readdirSync(modules + '@bsv/sdk/LICENSES').filter(f => f.endsWith('.txt')).sort()
      const sections: Array<[string, string]> = [
        ['@bsv/sdk LICENSE.txt', read('@bsv/sdk/LICENSE.txt')],
        ['@bsv/sdk THIRD_PARTY_NOTICES.md', read('@bsv/sdk/THIRD_PARTY_NOTICES.md')],
        ...sdkLicences.map((f): [string, string] => [`@bsv/sdk LICENSES/${f}`, read(`@bsv/sdk/LICENSES/${f}`)]),
        ['react LICENSE', read('react/LICENSE')],
        ['react-dom LICENSE', read('react-dom/LICENSE')],
        ['scheduler LICENSE', read('scheduler/LICENSE')]
      ]
      this.emitFile({
        type: 'asset',
        fileName: 'THIRD-PARTY-LICENSES.txt',
        source: 'Third-party software bundled in app.js\n\n' +
          sections.map(([title, body]) => `===== ${title} =====\n\n${body}\n`).join('\n')
      })
    }
  }
}

export default defineConfig({
  base: '/bsv_settlement/app/',
  plugins: [react(), licences()],
  build: {
    outDir,
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
    modulePreload: { polyfill: false },
    reportCompressedSize: false,
    chunkSizeWarningLimit: 800,
    rolldownOptions: {
      output: {
        codeSplitting: false,
        entryFileNames: 'app.js',
        chunkFileNames: 'app-[name].js',
        assetFileNames: '[name][extname]',
        comments: { legal: true, annotation: false, jsdoc: false }
      }
    }
  }
})
