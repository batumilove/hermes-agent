/* Hermes security derivative: GHSA-vfj7-8cjw-p6xm. */
'use strict';

// A fixed ceiling protects every recursive consumer, including expansion's
// nested append calls. Options cannot turn this safety bound off.
const MAX_DEPTH = 128;

const depthError = () => {
  const error = new SyntaxError(`Brace pattern exceeds maximum nesting depth (${MAX_DEPTH})`);
  error.code = 'ERR_BRACES_MAX_DEPTH';
  return error;
};

const assertDepth = ast => {
  const pending = [{ node: ast, depth: 0 }];
  while (pending.length > 0) {
    const { node, depth } = pending.pop();
    if (!node.nodes) continue;
    if (depth > MAX_DEPTH) throw depthError();
    for (const child of node.nodes) {
      if (child.nodes) pending.push({ node: child, depth: depth + 1 });
    }
  }
};

module.exports = { MAX_DEPTH, depthError, assertDepth };
