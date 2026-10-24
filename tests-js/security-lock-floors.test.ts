import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

interface Lockfile {
  packages: Record<string, { version?: string }>
}

const floors: Record<string, Record<number, string>> = {
  'body-parser': { 1: '1.20.9' },
  electron: { 42: '42.11.16' },
  'fast-uri': { 3: '3.1.9' },
  'serialize-javascript': { 7: '7.1.3' },
  undici: { 6: '6.29.1', 7: '7.30.1' },
  zod: { 4: '4.6.6' },
}

const locks = [
  'package-lock.json',
  'website/package-lock.json',
  'scripts/whatsapp-bridge/package-lock.json',
  'plugins/platforms/photon/sidecar/package-lock.json',
]

function atLeast(version: string, floor: string): boolean {
  if (version.includes('-')) {return false}
  const actual = version.split('.').map(Number)
  const required = floor.split('.').map(Number)

  for (let i = 0; i < 3; i++) {
    if (actual[i] !== required[i]) {return actual[i] > required[i]}
  }

  return true
}

describe('reviewed dependency advisory boundaries', () => {
  for (const path of locks) {
    it(`keeps every ${path} resolution above its fixed boundary`, () => {
      const lock = JSON.parse(readFileSync(fileURLToPath(new URL(`../${path}`, import.meta.url)), 'utf8')) as Lockfile
      const vulnerable: string[] = []

      for (const [location, metadata] of Object.entries(lock.packages)) {
        if (!metadata.version) {continue}
        const name = location.split('node_modules/').at(-1) ?? ''
        const floor = floors[name]?.[Number(metadata.version.split('.')[0])]

        if (floor && !atLeast(metadata.version, floor)) {vulnerable.push(`${location}@${metadata.version}`)}
      }

      expect(vulnerable).toEqual([])
    })
  }
})
