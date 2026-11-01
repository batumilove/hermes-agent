'use strict'

// Legacy glob and serve-handler call the export; modern minimatch attaches its
// public helpers to this same function, so matching stays in the patched engine.
module.exports = require('minimatch-modern').minimatch
