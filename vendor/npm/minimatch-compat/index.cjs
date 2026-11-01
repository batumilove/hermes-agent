'use strict'

const modern = require('minimatch-modern')

class LegacyMinimatch extends modern.Minimatch {
  constructor(pattern, options) {
    super(pattern, options)
    // glob 7 reads the original segment and uses String.match on each matcher.
    // Keep the patched engine's objects and methods, adding only that interface.
    for (const parts of this.set) {
      for (const matcher of parts) {
        if (typeof matcher !== 'object') continue
        matcher._glob = matcher.pattern
        matcher[Symbol.match] = value => matcher.test(value) ? [value] : null
      }
    }
  }
}

// Copy helpers onto a separate function so modern consumers retain their exports.
const minimatch = (value, pattern, options) => modern.minimatch(value, pattern, options)
module.exports = Object.assign(minimatch, modern.minimatch, {Minimatch: LegacyMinimatch})
