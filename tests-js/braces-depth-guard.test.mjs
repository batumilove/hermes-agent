/** GHSA-vfj7-8cjw-p6xm: bounded processing through the installed website consumers. */
import { createHash } from 'node:crypto'
import { readFileSync, realpathSync } from 'node:fs'
import { createRequire } from 'node:module'
import { sep } from 'node:path'
import { expect, test } from 'vitest'

const websiteRequire = createRequire(new URL('../website/package.json', import.meta.url))
const micromatchRequire = createRequire(websiteRequire.resolve('micromatch'))
const braces = micromatchRequire('braces')
const micromatch = websiteRequire('micromatch')
const deepPattern = '('.repeat(4996) + '{a,b}' + ')'.repeat(4996)

const expectDepthRefusal = (run) => {
  let error
  try { run() } catch (caught) { error = caught }
  expect(error).toBeInstanceOf(SyntaxError)
  expect(error.code).toBe('ERR_BRACES_MAX_DEPTH')
  expect(error.message).not.toMatch(/call stack/i)
}

const nestedAST = (depth) => {
  const ast = { type: 'root', nodes: [] }
  let parent = ast
  for (let index = 0; index < depth; index++) {
    const child = { type: 'paren', nodes: [], parent }
    parent.nodes.push(child)
    parent = child
  }
  parent.nodes.push({ type: 'text', value: 'x', parent })
  return ast
}

test('micromatch and chokidar resolve the reviewed artifact used by the website', () => {
  const installed = realpathSync(micromatchRequire.resolve('braces'))
  const websiteModules = realpathSync(new URL('../website/node_modules', import.meta.url))
  expect(installed.startsWith(websiteModules + sep)).toBe(true)
  const metadata = micromatchRequire('braces/package.json')
  expect(metadata.name).toBe('@hermes-security/braces')
  expect(metadata.version).toBe('3.0.3-hermes.1')
  const chokidarRequire = createRequire(websiteRequire.resolve('chokidar'))
  expect(chokidarRequire.resolve('braces')).toBe(micromatchRequire.resolve('braces'))
  const provenance = micromatchRequire('braces/SOURCE.json')
  expect(provenance.upstream.commit).toBe('74b2db2938fad48a2ea54a9c8bf27a37a62c350d')
  for (const [name, digest] of Object.entries(provenance.patched_sha256)) {
    const contents = readFileSync(micromatchRequire.resolve('braces/' + name))
    expect(createHash('sha256').update(contents).digest('hex'), name).toBe(digest)
  }
})

for (const method of ['parse', 'compile', 'expand', 'stringify', 'create']) {
  test(`${method} refuses deep patterns within the existing input length cap`, () => {
    expect(deepPattern.length).toBeLessThan(10000)
    expectDepthRefusal(() => braces[method](deepPattern))
    expectDepthRefusal(() => braces[method](deepPattern, {
      maxDepth: Infinity, maxLength: Infinity, rangeLimit: false,
    }))
  })
}

for (const method of ['compile', 'expand', 'stringify']) {
  test(`${method} checks caller supplied AST depth before walking or mutating it`, () => {
    const ast = nestedAST(129)
    expectDepthRefusal(() => braces[method](ast))
    expect(Object.hasOwn(ast, 'queue')).toBe(false)
    const cycle = { type: 'root', nodes: [] }
    cycle.nodes.push(cycle)
    expectDepthRefusal(() => braces[method](cycle))
    expect(Object.hasOwn(cycle, 'queue')).toBe(false)
  })
}

for (const [open, close] of [['{', '}'], ['(', ')'], ['{(', ')}']]) {
  test(`all syntax containers count for ${open} and balanced or unterminated input`, () => {
    expectDepthRefusal(() => braces.parse(open.repeat(129) + 'x' + close.repeat(129)))
    expectDepthRefusal(() => braces.parse(open.repeat(129) + 'x'))
  })
}

test('allowed depth, quoted/escaped delimiters, and wide sibling patterns remain usable', () => {
  const boundary = '('.repeat(128) + 'x' + ')'.repeat(128)
  expect(braces.compile(boundary)).toBe(boundary)
  expect(braces.stringify(nestedAST(128))).toBe('x')
  const quoted = '"' + '('.repeat(4000) + '"'
  expect(braces.stringify(quoted)).toBe('('.repeat(4000))
  expect(braces.stringify('\\('.repeat(1000))).toBe('('.repeat(1000))
  expect(braces.compile('{a,b}'.repeat(1000))).toBe('(a|b)'.repeat(1000))
})

test('normal alternatives, ranges, options and actual consumer expansion retain their results', () => {
  const pattern = 'src/{a,{b,c}}/{01..03}.js'
  const expanded = [
    'src/a/01.js', 'src/a/02.js', 'src/a/03.js',
    'src/b/01.js', 'src/b/02.js', 'src/b/03.js',
    'src/c/01.js', 'src/c/02.js', 'src/c/03.js',
  ]
  expect(braces.expand(pattern)).toEqual(expanded)
  expect(braces.compile(pattern)).toBe('src/(a|(b|c))/(0[1-3]).js')
  expect(braces.stringify(pattern)).toBe(pattern)
  expect(braces.expand('{a,a,,b}', { noempty: true, nodupes: true })).toEqual(['a', 'b'])
  expect(() => braces.expand('{1..1001}')).toThrow(RangeError)
  expect(braces([pattern], { expand: true })).toEqual(expanded)
  expect(micromatch.braceExpand(pattern)).toEqual(expanded)
  expectDepthRefusal(() => micromatch.braces(deepPattern))
  expectDepthRefusal(() => micromatch.braceExpand(deepPattern))
})
