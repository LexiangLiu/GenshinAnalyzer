import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'

const directory = fileURLToPath(new URL('.', import.meta.url))
const pin = JSON.parse(readFileSync(join(directory, 'upstream.json'), 'utf8'))
const checkout = process.argv[2]
if (!checkout) throw new Error('Usage: node tools/go_damage/build.mjs <pinned upstream checkout>')
const upstream = resolve(checkout)
const git = (...args) => execFileSync('git', ['-C', upstream, ...args], { encoding: 'utf8' }).trim()
if (git('rev-parse', 'HEAD') !== pin.commit || git('status', '--porcelain')) {
  throw new Error(`Expected a clean Genshin Optimizer checkout at ${pin.commit}`)
}
const tools = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8')).dependencies
const lock = JSON.parse(readFileSync(join(directory, 'package-lock.json'), 'utf8'))
for (const [name, version] of Object.entries(tools)) {
  if (lock.packages[`node_modules/${name}`]?.version !== version) {
    throw new Error(`Build dependency lock mismatch: ${name}`)
  }
}
const result = await build({
  absWorkingDir: upstream,
  stdin: {
    contents: readFileSync(join(directory, 'bridge.ts'), 'utf8'),
    resolveDir: upstream,
    sourcefile: 'artifact_sim_go_bridge.ts',
    loader: 'ts',
  },
  tsconfig: join(upstream, 'tsconfig.base.json'),
  nodePaths: [join(directory, 'node_modules')],
  bundle: true,
  write: false,
  metafile: true,
  platform: 'node',
  format: 'esm',
  target: `node${pin.node_major}`,
  minifyWhitespace: true,
  legalComments: 'eof',
  banner: { js: '// Generated from Genshin Optimizer and the ArtifactSimulator adapter. See adjacent MIT notices.' },
})
const output = result.outputFiles[0].contents
const runtime = resolve(directory, '../../artifact_sim/go_damage')
mkdirSync(runtime, { recursive: true })
writeFileSync(join(runtime, 'bridge.mjs'), output)
writeFileSync(join(runtime, 'LICENSE-genshin-optimizer.txt'), readFileSync(join(upstream, 'LICENSE')))
writeFileSync(join(runtime, 'LICENSE-fflate.txt'), readFileSync(join(directory, 'node_modules/fflate/LICENSE')))
const internalLibraries = [...new Set(Object.keys(result.metafile.inputs)
  .filter(path => path.startsWith('libs/'))
  .map(path => path.split('/').slice(0, 3).join('/')))].sort()
writeFileSync(join(runtime, 'manifest.json'), JSON.stringify({
  ...pin, build_dependencies: tools, internal_libraries: internalLibraries,
  sha256: createHash('sha256').update(output).digest('hex'),
}, null, 2) + '\n')
console.log(`Built ${output.length} bytes from ${internalLibraries.join(', ')}`)
