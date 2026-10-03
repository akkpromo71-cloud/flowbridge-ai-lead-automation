import { existsSync, mkdirSync, readFileSync, writeFileSync, openSync, closeSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { createServer } from 'node:net';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const profile = process.argv.find((arg) => arg.startsWith('--profile='))?.split('=')[1] ?? 'demo';
if (!['demo', 'controlled'].includes(profile)) throw new Error('Only demo/controlled local profiles are supported');
const port = profile === 'controlled' ? 5683 : 5681;
const brokerPort = profile === 'controlled' ? 5684 : 5682;
const baseUrl = `http://127.0.0.1:${port}`;
const databaseName = profile === 'controlled' ? 'ai_leads_n8n_controlled' : 'ai_leads_n8n';
const runtime = join(process.env.LOCALAPPDATA, 'AILeadAutomationPro', 'n8n-runtime');
const data = join(process.env.LOCALAPPDATA, 'AILeadAutomationPro', `n8n-${profile}`);
const cliPath = join(runtime, 'node_modules', 'n8n', 'bin', 'n8n');
const markerPath = join(data, 'project-owner.json');
const pidPath = join(data, 'process.json');
const workflowIds = ['aiLeadProcessing', 'aiLeadCommunication', 'aiLeadMailSync', 'aiLeadFollowupCheck'];
const command = process.argv[2] ?? 'start';
// Optional local launcher readiness budget; controlled callers keep the default.
const readinessSeconds = Number(process.argv.find((arg) => arg.startsWith('--ready-timeout='))?.split('=')[1] ?? 180);
if (!Number.isInteger(readinessSeconds) || readinessSeconds < 1 || readinessSeconds > 600) throw new Error('Readiness timeout must be 1..600 seconds');

if (existsSync(data)) {
  if (!existsSync(markerPath)) throw new Error('Refusing to use an unowned n8n directory');
  const owner = JSON.parse(readFileSync(markerPath, 'utf8'));
  if (owner.root !== root || owner.purpose !== `synthetic-${profile}`) throw new Error('n8n directory belongs to another project');
} else {
  mkdirSync(data, { recursive: true });
  writeFileSync(markerPath, JSON.stringify({ root, purpose: `synthetic-${profile}` }, null, 2));
}
if (!existsSync(cliPath)) throw new Error('Install the pinned local n8n dependencies first; see docs/n8n.md');
const runtimeOwner = JSON.parse(readFileSync(join(runtime, 'project-owner.json'), 'utf8').replace(/^\uFEFF/, ''));
if (runtimeOwner.projectRoot !== root || runtimeOwner.purpose !== 'synthetic-demo-runtime') throw new Error('n8n runtime belongs to another project');

const encryptionPath = join(data, 'encryption-key');
if (!existsSync(encryptionPath)) writeFileSync(encryptionPath, randomBytes(32).toString('hex'), { mode: 0o600 });
// Copy only ordinary OS runtime variables. Existing n8n settings and provider keys
// from the user's shell cannot leak into this isolated demo process.
const inheritedNames = ['PATH', 'Path', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'ComSpec', 'COMSPEC',
  'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
  'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'PATHEXT'];
const env = Object.fromEntries(inheritedNames.filter((key) => process.env[key]).map((key) => [key, process.env[key]]));
Object.assign(env, {
  NODE_ENV: 'production', N8N_USER_FOLDER: data,
  N8N_ENCRYPTION_KEY: readFileSync(encryptionPath, 'utf8').trim(),
  N8N_LISTEN_ADDRESS: '127.0.0.1', N8N_HOST: '127.0.0.1', N8N_PORT: String(port), N8N_PROTOCOL: 'http',
  N8N_EDITOR_BASE_URL: baseUrl, N8N_WEBHOOK_URL: `${baseUrl}/`,
  N8N_RUNNERS_BROKER_PORT: String(brokerPort), N8N_RUNNERS_BROKER_LISTEN_ADDRESS: '127.0.0.1',
  N8N_RUNNERS_MODE: 'external', N8N_RUNNERS_AUTH_TOKEN: randomBytes(32).toString('hex'), N8N_SECURE_COOKIE: 'false',
  N8N_DIAGNOSTICS_ENABLED: 'false', N8N_VERSION_NOTIFICATIONS_ENABLED: 'false',
  N8N_TEMPLATES_ENABLED: 'false', N8N_PERSONALIZATION_ENABLED: 'false',
  N8N_DISABLED_MODULES: 'chat-hub,workflow-builder,instance-ai,mcp,mcp-registry',
  N8N_COMMUNITY_PACKAGES_ENABLED: 'false', N8N_BLOCK_ENV_ACCESS_IN_NODE: 'true',
  N8N_UNVERIFIED_PACKAGES_ENABLED: 'false', N8N_RUNNERS_TASK_TIMEOUT: '60',
  N8N_COMPRESSION_NODE_MAX_DECOMPRESSED_SIZE_BYTES: '268435456', N8N_COMPRESSION_NODE_MAX_ZIP_ENTRIES: '1000',
  GENERIC_TIMEZONE: 'UTC', TZ: 'UTC',
  DB_TYPE: 'postgresdb', DB_POSTGRESDB_HOST: '127.0.0.1', DB_POSTGRESDB_PORT: '15432',
  DB_POSTGRESDB_DATABASE: databaseName, DB_POSTGRESDB_USER: databaseName, DB_POSTGRESDB_PASSWORD: '',
  DB_POSTGRESDB_POOL_SIZE: '5', DB_POSTGRESDB_CONNECTION_TIMEOUT: '20000',
  EXECUTIONS_DATA_SAVE_ON_ERROR: 'none', EXECUTIONS_DATA_SAVE_ON_SUCCESS: 'none',
  EXECUTIONS_DATA_SAVE_MANUAL_EXECUTIONS: 'false', EXECUTIONS_DATA_SAVE_ON_PROGRESS: 'false',
  EXECUTIONS_DATA_PRUNE: 'true', EXECUTIONS_DATA_MAX_AGE: '168',
});

function runCli(args) {
  const result = spawnSync(process.execPath, [cliPath, ...args], {
    env, cwd: data, windowsHide: true, encoding: 'utf8', timeout: profile === 'controlled' ? 600000 : 180000, maxBuffer: 32 * 1024 * 1024,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  writeFileSync(join(data, 'cli.log'), `${new Date().toISOString()} ${args[0]}\n${result.stdout ?? ''}${result.stderr ?? ''}`, { flag: 'a' });
  if (result.error || result.status !== 0) throw new Error(`n8n ${args[0]} failed; see ${join(data, 'cli.log')}`);
  return result.stdout;
}
function isOurProcessRunning() {
  if (!existsSync(pidPath)) return false;
  const record = JSON.parse(readFileSync(pidPath, 'utf8'));
  if (record.cliPath !== cliPath || !Number.isSafeInteger(record.pid)) throw new Error('Invalid project process record');
  try { process.kill(record.pid, 0); return true; } catch { return false; }
}
function freePort(port) {
  return new Promise((ok, bad) => {
    const server = createServer();
    server.once('error', () => bad(new Error(`Port ${port} is occupied; no existing process was changed`)));
    server.listen(port, '127.0.0.1', () => server.close(ok));
  });
}
function readServiceTokens() {
  const values = {};
  const text = profile === 'controlled'
    ? Object.entries(JSON.parse(readFileSync(join(process.env.LOCALAPPDATA, 'AILeadAutomationPro', 'controlled-runtime.json'), 'utf8'))).filter(([key]) => ['MODE', 'INTERNAL_TOKEN', 'N8N_WEBHOOK_TOKEN'].includes(key)).map(([key, value]) => `${key}=${value}`).join('\n')
    : readFileSync(join(root, '.env'), 'utf8');
  for (const line of text.split(/\r?\n/)) {
    const match = /^(INTERNAL_TOKEN|N8N_WEBHOOK_TOKEN|MODE)=(.*)$/.exec(line.trim());
    if (match) values[match[1]] = match[2].replace(/^['"]|['"]$/g, '');
  }
  if (values.MODE !== profile) throw new Error('Local profile/service token mode mismatch');
  if (!values.INTERNAL_TOKEN || !values.N8N_WEBHOOK_TOKEN || values.INTERNAL_TOKEN.length < 32 ||
    values.N8N_WEBHOOK_TOKEN.length < 32 || values.INTERNAL_TOKEN === values.N8N_WEBHOOK_TOKEN) {
    throw new Error('Generate two distinct service tokens with the application setup command first');
  }
  return values;
}

async function configureInitialOwner() {
  const settingsResponse = await fetch(`${baseUrl}/rest/settings`, { signal: AbortSignal.timeout(10000) });
  if (!settingsResponse.ok) throw new Error('Could not verify local n8n owner setup state');
  const settings = await settingsResponse.json();
  if (!settings.data?.userManagement?.showSetupOnFirstLoad) return;
  const ownerPath = join(data, 'owner-access.json');
  const account = existsSync(ownerPath) ? JSON.parse(readFileSync(ownerPath, 'utf8')) :
    { email: 'owner@example.invalid', firstName: 'Demo', lastName: 'Operator',
      password: `Demo!${randomBytes(24).toString('base64url')}` };
  if (!existsSync(ownerPath)) writeFileSync(ownerPath, JSON.stringify(account, null, 2), { mode: 0o600 });
  const response = await fetch(`${baseUrl}/rest/owner/setup`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: baseUrl },
    body: JSON.stringify(account), signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw new Error(`Initial n8n owner setup failed (HTTP ${response.status}); access file retained locally`);
  console.log(`Protected the new n8n editor with a unique local account. Access file: ${ownerPath}`);
}

async function waitUntilReady() {
  const deadline = Date.now() + readinessSeconds * 1000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${baseUrl}/healthz/readiness`, { signal: AbortSignal.timeout(Math.max(1, Math.min(1500, deadline - Date.now()))) });
      if (response.ok) return;
    } catch { /* First startup loads and verifies the installed node types. */ }
    await new Promise((ok) => setTimeout(ok, Math.max(0, Math.min(1000, deadline - Date.now()))));
  }
  throw new Error(`n8n did not become ready; see logs in ${data}`);
}

async function verifyPublication({ repairFailed = false } = {}) {
  const ownerPath = join(data, 'owner-access.json');
  if (!existsSync(ownerPath)) throw new Error('Local owner access file is missing; verify workflow activation in the n8n editor');
  const owner = JSON.parse(readFileSync(ownerPath, 'utf8'));
  const login = await fetch(`${baseUrl}/rest/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: baseUrl },
    body: JSON.stringify({ emailOrLdapLoginId: owner.email, password: owner.password }), signal: AbortSignal.timeout(30000),
  });
  if (!login.ok) throw new Error('Cannot verify workflow publication with the saved local owner account');
  const cookie = login.headers.getSetCookie().map((value) => value.split(';')[0]).join('; ');
  const headers = { Cookie: cookie, 'Content-Type': 'application/json', Origin: baseUrl };
  const repaired = new Set();
  for (let attempt = 0; attempt < 30; attempt += 1) {
    let ready = 0;
    for (const id of workflowIds) {
      const response = await fetch(`${baseUrl}/rest/workflows/${id}/publication-status`, { headers, signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error(`Cannot read publication status for ${id}`);
      const { data: status } = await response.json();
      if (status.status === 'published' && status.triggers.length > 0 && status.triggers.every((trigger) => trigger.status === 'activated')) {
        ready += 1; continue;
      }
      if (repairFailed && !status.pendingVersionId && !repaired.has(id)) {
        const detailsResponse = await fetch(`${baseUrl}/rest/workflows/${id}`, { headers, signal: AbortSignal.timeout(10000) });
        if (!detailsResponse.ok) throw new Error(`Cannot inspect ${id} before publication recovery`);
        const { data: details } = await detailsResponse.json();
        if (!details.activeVersionId) throw new Error(`${id} is unpublished; it was not activated automatically`);
        const result = await fetch(`${baseUrl}/rest/workflows/${id}/activate`, {
          method: 'POST', headers, body: JSON.stringify({ versionId: details.activeVersionId }), signal: AbortSignal.timeout(30000),
        });
        if (!result.ok) throw new Error(`Could not restore publication of ${id} (HTTP ${result.status})`);
        repaired.add(id);
      }
    }
    if (ready === workflowIds.length) {
      console.log('Verified all four published workflows and their activated triggers.');
      return;
    }
    await new Promise((ok) => setTimeout(ok, 1000));
  }
  throw new Error('One or more n8n workflow triggers are not activated; inspect the local editor and logs');
}

if (command === 'setup') {
  if (isOurProcessRunning()) throw new Error('Stop this project n8n before importing or publishing workflows');
  const tokens = readServiceTokens();
  const temporaryPath = join(data, `credentials-${randomUUID()}.json`);
  const credentials = [
    { id: 'aiLeadInternalAuth', name: 'AI Lead Internal API', type: 'httpHeaderAuth', data: { name: 'X-Internal-Token', value: tokens.INTERNAL_TOKEN } },
    { id: 'aiLeadWebhookAuth', name: 'AI Lead Dispatch Webhook', type: 'httpHeaderAuth', data: { name: 'X-Webhook-Token', value: tokens.N8N_WEBHOOK_TOKEN } },
  ];
  try {
    writeFileSync(temporaryPath, JSON.stringify(credentials), { mode: 0o600 });
    runCli(['import:credentials', `--input=${temporaryPath}`]);
  } finally {
    if (existsSync(temporaryPath)) rmSync(temporaryPath);
  }
  const workflowDirectory = profile === 'controlled' ? join(data, 'workflows') : join(root, 'n8n', 'workflows');
  if (profile === 'controlled') {
    const built = spawnSync(process.execPath, [join(root, 'n8n', 'build-workflows.mjs')], { env: { ...env, BUILD_BACKEND_URL: 'http://127.0.0.1:8001', BUILD_WORKFLOW_OUTPUT: workflowDirectory }, windowsHide: true, encoding: 'utf8' });
    if (built.status !== 0) throw new Error('Controlled workflow generation failed');
  }
  runCli(['import:workflow', '--separate', `--input=${workflowDirectory}`]);
  for (const id of workflowIds) runCli(['publish:workflow', `--id=${id}`]);
  writeFileSync(join(data, 'setup-complete.json'), JSON.stringify({ version: '2.40.7', workflowIds, at: new Date().toISOString() }, null, 2));
  console.log(`Imported credentials securely and published four local ${profile} workflows. Restart n8n to load changes.`);
} else if (command === 'start') {
  readServiceTokens();
  if (isOurProcessRunning()) {
    await waitUntilReady();
    await configureInitialOwner();
    await verifyPublication({ repairFailed: true });
    console.log(`Project n8n (${profile}) is already running: ${baseUrl}`);
  } else {
    await freePort(port); await freePort(brokerPort);
    const stdout = openSync(join(data, 'stdout.log'), 'a');
    const stderr = openSync(join(data, 'stderr.log'), 'a');
    const child = spawn(process.execPath, [cliPath, 'start'], {
      env, cwd: data, detached: true, windowsHide: true, stdio: ['ignore', stdout, stderr],
    });
    child.unref(); closeSync(stdout); closeSync(stderr);
    writeFileSync(pidPath, JSON.stringify({ pid: child.pid, cliPath, startedAt: new Date().toISOString() }, null, 2));
    await waitUntilReady();
    await configureInitialOwner();
    await verifyPublication({ repairFailed: true });
    console.log(`Project n8n (${profile}) is ready: ${baseUrl}`);
  }
} else if (command === 'verify') {
  await waitUntilReady();
  await verifyPublication();
} else if (command === 'export') {
  const destination = join(data, 'verified-exports'); mkdirSync(destination, { recursive: true });
  for (const id of workflowIds) runCli(['export:workflow', `--id=${id}`, '--pretty', `--output=${join(destination, `${id}.json`)}`]);
  console.log(`Exported four workflows to ${destination}; credentials were not exported.`);
} else if (command === 'help') {
  for (const operation of ['import:credentials', 'import:workflow', 'publish:workflow', 'export:workflow']) {
    console.log(runCli([operation, '--help']));
  }
} else {
  throw new Error('Use setup, start, verify, export or help; stop is handled by scripts/stop-n8n.ps1');
}
