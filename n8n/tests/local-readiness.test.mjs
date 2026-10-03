// Execute the existing readiness function with a synthetic clock/transport only.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../manage-local.mjs', import.meta.url), 'utf8');
const functionSource = source.match(/async function waitUntilReady\(\) \{[\s\S]*?\r?\n\}\r?\n/)?.[0];
assert.ok(functionSource, 'Test must execute the actual launcher function');

function syntheticReadiness(ready) {
  let now = 0;
  let requests = 0;
  const signals = [];
  const context = {
    readinessSeconds: 3, baseUrl: 'http://127.0.0.1:5681', data: 'synthetic-runtime',
    Date: { now: () => now },
    AbortSignal: { timeout: (ms) => { signals.push(ms); return ms; } },
    fetch: async (url, options) => {
      assert.equal(url, 'http://127.0.0.1:5681/healthz/readiness');
      requests++;
      if (ready) return { ok: true };
      now += options.signal;
      throw new Error('synthetic timeout');
    },
    setTimeout: (callback, ms) => { now += ms; callback(); },
  };
  return { promise: runInNewContext(`${functionSource}\nwaitUntilReady()`, context),
    stats: () => ({ now, requests, signals }) };
}

test('ready n8n completes after one local readiness probe', async () => {
  const run = syntheticReadiness(true);
  await run.promise;
  assert.equal(run.stats().requests, 1);
});
test('unavailable n8n stops at the wall-clock deadline, including HTTP timeouts', async () => {
  const run = syntheticReadiness(false);
  await assert.rejects(run.promise, /n8n did not become ready/);
  assert.deepEqual(run.stats(), { now: 3000, requests: 2, signals: [1500, 500] });
});
