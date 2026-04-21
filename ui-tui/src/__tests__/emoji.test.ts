import { describe, expect, it } from 'vitest'

import { ensureEmojiPresentation } from '../lib/emoji.js'

const VS16 = '\uFE0F'

describe('ensureEmojiPresentation', () => {
  it('passes through ASCII unchanged', () => {
    expect(ensureEmojiPresentation('hello world')).toBe('hello world')
    expect(ensureEmojiPresentation('')).toBe('')
  })

  it('passes through emoji that already defaults to emoji presentation', () => {
    expect(ensureEmojiPresentation('🚀 rocket')).toBe('🚀 rocket')
    expect(ensureEmojiPresentation('😀')).toBe('😀')
  })

  it('injects VS16 after text-default emoji codepoints', () => {
    expect(ensureEmojiPresentation('⚠ careful')).toBe(`⚠${VS16} careful`)
    expect(ensureEmojiPresentation('ℹ info')).toBe(`ℹ${VS16} info`)
    expect(ensureEmojiPresentation('love ❤ you')).toBe(`love ❤${VS16} you`)
    expect(ensureEmojiPresentation('✔ done')).toBe(`✔${VS16} done`)
  })

  it('is idempotent when VS16 is already present', () => {
    const already = `⚠${VS16} ℹ${VS16} ❤${VS16}`

    expect(ensureEmojiPresentation(already)).toBe(already)
    expect(ensureEmojiPresentation(ensureEmojiPresentation('⚠'))).toBe(`⚠${VS16}`)
  })

  it('leaves keycap sequences alone (digit + U+20E3 renders without VS16)', () => {
    expect(ensureEmojiPresentation('1\u20e3')).toBe('1\u20e3')
  })

  it('does not inject inside ZWJ sequences', () => {
    const family = '\u2764\u200d\ud83d\udd25'

    expect(ensureEmojiPresentation(family)).toBe(family)
  })

  it('handles mixed content', () => {
    expect(ensureEmojiPresentation('⚠ path: /tmp/x ❤ done')).toBe(`⚠${VS16} path: /tmp/x ❤${VS16} done`)
  })
})
