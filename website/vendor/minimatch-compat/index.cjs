'use strict';

const modern = require('minimatch-modern');
const compat = modern.minimatch;

Object.assign(compat, modern);

module.exports = compat;
