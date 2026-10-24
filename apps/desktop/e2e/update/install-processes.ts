import * as fs from 'node:fs'
import * as path from 'node:path'

import type { ProcInfo } from '../core/process-census'

/** The update lane owns a private packaged release directory, including its helpers. */
export function packagedProcesses(directory: string): ProcInfo[] {
  const prefix = fs.realpathSync(directory) + path.sep

  return fs.readdirSync('/proc').flatMap(entry => {
    const pid = Number(entry)
    if (!Number.isInteger(pid) || pid === process.pid) return []

    try {
      if (!fs.readlinkSync(`/proc/${pid}/exe`).startsWith(prefix)) return []
      const cmdline = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').join(' ').trim()
      const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8')
      const ppid = Number(stat.slice(stat.lastIndexOf(')') + 2).split(' ')[1])

      return cmdline ? [{ pid, ppid, cmdline }] : []
    } catch {
      return [] // Process exited during the census.
    }
  })
}

/** Match the exact main executable; helpers and descendant Electron processes are not mains. */
export function packagedMainProcesses(executable: string): ProcInfo[] {
  const target = fs.realpathSync(executable)
  const candidates = packagedProcesses(path.dirname(target)).filter(proc => {
    try {
      return fs.readlinkSync(`/proc/${proc.pid}/exe`) === target
    } catch {
      return false // Process exited during the census.
    }
  })
  const pids = new Set(candidates.map(proc => proc.pid))

  return candidates.filter(proc => !pids.has(proc.ppid))
}
