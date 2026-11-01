import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'
import { expect, test } from 'vitest'

const requireRoot = createRequire(new URL('../package.json', import.meta.url))

test('the node-gyp build manifest and locked graph contain only production dependencies', () => {
  const manifest = JSON.parse(readFileSync(new URL('../nix/node-gyp-11-4-0-package.json', import.meta.url), 'utf8'))
  const lock = JSON.parse(readFileSync(new URL('../nix/node-gyp-11-4-0-package-lock.json', import.meta.url), 'utf8'))
  expect(manifest).not.toHaveProperty('devDependencies')
  expect(lock.packages['']).not.toHaveProperty('devDependencies')
  expect(lock.packages[''].name).toBe(manifest.name)
  expect(lock.packages[''].version).toBe(manifest.version)
  expect(lock.packages[''].dependencies).toEqual(manifest.dependencies)
  expect(Object.values(lock.packages).every(metadata => !metadata.dev)).toBe(true)
})

function fixture() {
  const directory = mkdtempSync(path.join(tmpdir(), 'hermes-glob-consumer-'))
  mkdirSync(path.join(directory, 'sub'))
  for (const name of ['a.txt', 'b.js', '.hidden.txt', 'sub/c.txt', 'sub/d.js']) {
    writeFileSync(path.join(directory, name), name)
  }
  return directory
}

test('legacy callback and synchronous glob consumers keep matching and cleanup semantics', async () => {
  const directory = fixture()
  const glob = requireRoot('glob')
  try {
    const cases = [
      ['**/*.txt', {}, ['a.txt', 'sub/c.txt']],
      ['**/*.{js,txt}', { ignore: 'sub/**' }, ['a.txt', 'b.js']],
      ['**/*.txt', { dot: true }, ['.hidden.txt', 'a.txt', 'sub/c.txt']],
      ['@(a|b).*', {}, ['a.txt', 'b.js']],
      ['missing*.txt', { nonull: true }, ['missing*.txt']],
    ]
    for (const [pattern, options, expected] of cases) {
      expect(glob.sync(pattern, { cwd: directory, ...options }).sort()).toEqual(expected)
      expect((await promisify(glob)(pattern, { cwd: directory, ...options })).sort()).toEqual(expected)
    }
    const { FileList } = requireRoot('filelist')
    const files = new FileList(path.join(directory, '**/*.txt')).exclude(path.join(directory, 'sub/**'))
    expect(files.toArray()).toEqual([path.join(directory, 'a.txt')])
    const rimraf = requireRoot('rimraf')
    await promisify(rimraf)(path.join(directory, 'sub/*.txt'))
    expect(existsSync(path.join(directory, 'sub/c.txt'))).toBe(false)
    expect(existsSync(path.join(directory, 'sub/d.js'))).toBe(true)
    rimraf.sync(path.join(directory, '*.txt'))
    expect(existsSync(path.join(directory, 'a.txt'))).toBe(false)
    expect(existsSync(path.join(directory, '.hidden.txt'))).toBe(true)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})

test('ASAR used by the Windows installer preserves archive metadata and unpacked contents', async () => {
  const directory = fixture()
  const asar = createRequire(requireRoot.resolve('electron-winstaller'))('@electron/asar')
  try {
    writeFileSync(path.join(directory, 'package.json'), JSON.stringify({ name: 'fixture', version: '1.0.0' }))
    const archive = path.join(directory, 'app.asar')
    // Keep the output outside the walked tree.
    const input = path.join(directory, 'sub')
    writeFileSync(path.join(input, 'package.json'), JSON.stringify({ name: 'fixture', version: '1.0.0' }))
    await asar.createPackageWithOptions(input, archive, { unpack: '**/*.txt' })
    expect(JSON.parse(asar.extractFile(archive, 'package.json').toString()).name).toBe('fixture')
    expect(asar.extractFile(archive, 'c.txt').toString()).toBe('sub/c.txt')
    expect(asar.listPackage(archive)).toContain('/d.js')
    asar.extractAll(archive, path.join(directory, 'extracted'))
    expect(existsSync(path.join(directory, 'extracted/c.txt'))).toBe(true)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})

test('modern glob cache consumers keep cache verification and removal working', async () => {
  const directory = fixture()
  const cache = requireRoot('cacache')
  try {
    const store = path.join(directory, 'cache')
    await cache.put(store, 'fixture', 'cache-content')
    expect((await cache.get(store, 'fixture')).data.toString()).toBe('cache-content')
    expect((await cache.verify(store)).verifiedContent).toBe(1)
    await cache.rm.entry(store, 'fixture')
    await cache.verify(store)
    expect(Object.keys(await cache.ls(store))).toEqual([])
    const modern = createRequire(requireRoot.resolve('get-windows'))('glob')
    expect((await modern.glob('**/*.txt', { cwd: directory })).sort()).toEqual(['a.txt', 'sub/c.txt'])
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
