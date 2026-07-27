/** Intentional Shadow v2 Desktop E2E negative control. DO NOT MERGE. */
import { expect, test } from '@playwright/test'

test('Shadow v2 detects Desktop E2E failure', () => {
  expect('intentional-negative-control').toBe('pass')
})
