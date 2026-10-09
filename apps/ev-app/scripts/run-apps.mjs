import { spawn, spawnSync } from 'node:child_process'

const packageManager = "npm"
const task = process.argv[2]
const apps = ['client', 'server']

if (!['dev', 'build', 'install'].includes(task)) {
  console.error('usage: node scripts/run-apps.mjs <dev|build|install>')
  process.exit(1)
}

const commandFor = (name) => {
  if (task === 'install') return { command: packageManager, args: ['install'] }
  return { command: packageManager, args: ['run', task] }
}

if (task !== 'dev') {
  for (const app of apps) {
    const cmd = commandFor(app)
    const result = spawnSync(cmd.command, cmd.args, { cwd: new URL('../' + app + '/', import.meta.url), stdio: 'inherit', shell: process.platform === 'win32' })
    if (result.status !== 0) process.exit(result.status ?? 1)
  }
  process.exit(0)
}

const children = apps.map(app => {
  const cmd = commandFor(app)
  return spawn(cmd.command, cmd.args, { cwd: new URL('../' + app + '/', import.meta.url), stdio: 'inherit', shell: process.platform === 'win32' })
})
const stop = () => children.forEach(child => { if (!child.killed) child.kill('SIGTERM') })
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
children.forEach(child => child.on('exit', code => {
  if (code !== 0 && code != null) { stop(); process.exitCode = code }
}))
await Promise.all(children.map(child => new Promise(resolve => child.on('exit', resolve))))
