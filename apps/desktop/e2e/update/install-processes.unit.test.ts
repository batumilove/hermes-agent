import { spawn } from 'node:child_process'
import { once } from 'node:events'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { expect, test } from 'vitest'

import { packagedMainProcesses, packagedProcesses } from './install-processes'

test.skipIf(process.platform !== 'linux')('finds a renamed main and its child without home env, excluding helpers and other installs', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'install census '))
  const release = path.join(root, 'release')
  const other = path.join(root, 'release-other')
  fs.mkdirSync(release)
  fs.mkdirSync(other)
  const main = path.join(release, 'Hermes')
  const helper = path.join(release, 'chrome_crashpad_handler')
  const bystander = path.join(other, 'Hermes')
  for (const file of [main, helper, bystander]) fs.copyFileSync(process.execPath, file)
  const hold = 'setInterval(() => {}, 1000)'
  const env = { ...process.env }
  delete env.HERMES_HOME
  const app = spawn(main, ['-e', `const {spawn}=require('node:child_process'); process.title='Hermes --no-sandbox'; const child=spawn(process.execPath,['-e',${JSON.stringify(hold)}],{stdio:'ignore'}); child.on('spawn',()=>process.send(child.pid)); ${hold}`], { env, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] })
  const crashpad = spawn(helper, ['-e', hold], { env, stdio: 'ignore' })
  const unrelated = spawn(bystander, ['-e', hold], { env, stdio: 'ignore' })
  let childPid: number | undefined
  try {
    ;[childPid] = await once(app, 'message') as [number]
    await expect.poll(() => packagedProcesses(release).map(proc => proc.pid)).toEqual(expect.arrayContaining([app.pid, childPid, crashpad.pid]))
    expect(packagedProcesses(release).map(proc => proc.pid)).not.toContain(unrelated.pid)
    expect(packagedMainProcesses(main).map(proc => proc.pid)).toEqual([app.pid])
  } finally {
    const exits = [app, crashpad, unrelated].map(proc => once(proc, 'exit'))
    if (childPid) process.kill(childPid, 'SIGKILL')
    for (const proc of [app, crashpad, unrelated]) proc.kill('SIGKILL')
    await Promise.all(exits)
    fs.rmSync(root, { recursive: true, force: true })
  }
})
