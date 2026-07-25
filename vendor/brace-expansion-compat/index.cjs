'use strict';

const modern = require('brace-expansion-modern');

function expand(pattern, options) {
  return modern.expand(pattern, options);
}

Object.assign(expand, modern);
Object.defineProperty(expand, '__hermesPatchedVersion', {
  value: '5.0.8',
  enumerable: false,
});

module.exports = expand;
