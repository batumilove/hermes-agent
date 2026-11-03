/** Exercise the installed legacy and modern consumers of the patched matchers. */
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from 'vitest'

const rootRequire = createRequire(new URL('../package.json', import.meta.url))
const websiteRequire = createRequire(new URL('../website/package.json', import.meta.url))

test('legacy glob and filelist retain recursive, brace and exclusion matching', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'hermes-glob-'))
  try {
    await mkdir(join(directory, 'nested'))
    await writeFile(join(directory, 'nested', 'keep.txt'), 'kept')
    await writeFile(join(directory, 'nested', 'skip.txt'), 'excluded')
    const glob = rootRequire('glob')
    expect(glob.sync('**/{keep,skip}.txt', { cwd: directory, ignore: '**/skip.txt' })).toEqual(['nested/keep.txt'])
    const asyncMatches = await new Promise((resolve, reject) => {
      glob('**/keep.txt', { cwd: directory }, (error, matches) => error ? reject(error) : resolve(matches))
    })
    expect(asyncMatches).toEqual(['nested/keep.txt'])
    const { FileList } = rootRequire('filelist')
    const files = new FileList(join(directory, '**', '*.txt'))
    files.exclude(join(directory, '**', 'skip.txt'))
    expect(files.toArray()).toEqual([join(directory, 'nested', 'keep.txt')])
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('modern archive and cache consumers traverse files with the patched glob', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'hermes-archive-'))
  try {
    const source = join(directory, 'source')
    await mkdir(source)
    await writeFile(join(source, 'hello.txt'), 'archive payload')
    const asar = await import(rootRequire.resolve('@electron/asar'))
    const archive = join(directory, 'app.asar')
    await asar.createPackage(source, archive)
    expect(asar.extractFile(archive, 'hello.txt').toString()).toBe('archive payload')
    const cacache = rootRequire('cacache')
    const cache = join(directory, 'cache')
    await cacache.put(cache, 'fixture', 'cache payload')
    const verified = await cacache.verify(cache)
    expect(verified.verifiedContent).toBe(1)
    expect((await cacache.get(cache, 'fixture')).data.toString()).toBe('cache payload')
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})

test('website serving applies a glob header rule through the callable adapter', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'hermes-serve-'))
  const handler = websiteRequire('serve-handler')
  await writeFile(join(directory, 'page.html'), 'site payload')
  const server = createServer((request, response) => handler(request, response, {
    public: directory,
    cleanUrls: false,
    headers: [{ source: '**/*.html', headers: [{ key: 'X-Hermes-Fixture', value: 'matched' }] }],
  }))
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/page.html`)
    expect(response.status).toBe(200)
    expect(await response.text()).toBe('site payload')
    expect(response.headers.get('x-hermes-fixture')).toBe('matched')
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(directory, { recursive: true, force: true })
  }
})
