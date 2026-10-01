// Generate portable n8n 2.40.7 workflows. No business rules or secrets belong here.
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const output = process.env.BUILD_WORKFLOW_OUTPUT ?? join(dirname(fileURLToPath(import.meta.url)), 'workflows');
mkdirSync(output, { recursive: true });
const internal = { httpHeaderAuth: { id: 'aiLeadInternalAuth', name: 'AI Lead Internal API' } };
const webhookAuth = { httpHeaderAuth: { id: 'aiLeadWebhookAuth', name: 'AI Lead Dispatch Webhook' } };
const settings = {
  executionOrder: 'v1', timezone: 'UTC', executionTimeout: 180,
  saveDataErrorExecution: 'none', saveDataSuccessExecution: 'none',
  saveManualExecutions: false, saveExecutionProgress: false,
};
const baseUrl = process.env.BUILD_BACKEND_URL ?? process.env.AI_LEAD_INTERNAL_BASE_URL ?? 'http://127.0.0.1:8000';
if (!/^https?:\/\/[a-zA-Z0-9.:[\]-]+$/.test(baseUrl)) throw new Error('Invalid internal base URL');
const webhook = (path) => ({
  id: 'dispatch', name: 'Dispatch', type: 'n8n-nodes-base.webhook', typeVersion: 2.1,
  position: [0, 0], webhookId: path,
  parameters: { httpMethod: 'POST', path, authentication: 'headerAuth', responseMode: 'onReceived',
    options: { responseCode: { values: { responseCode: 202 } }, noResponseBody: true } },
  credentials: webhookAuth,
});
const httpStep = (name, step, position) => ({
  id: step, name, type: 'n8n-nodes-base.httpRequest', typeVersion: 4.4, position,
  parameters: {
    method: 'POST',
    url: `=${baseUrl}/internal/v1/jobs/{{ $('Dispatch').item.json.body.job_id }}/steps/${step}`,
    authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth',
    sendBody: true, specifyBody: 'json',
    jsonBody: '={{ { generation: $("Dispatch").item.json.body.generation } }}',
    options: { timeout: 90000 },
  }, credentials: internal, retryOnFail: false,
});
const isKind = (kind, name, position) => ({
  id: `is-${kind}`, name, type: 'n8n-nodes-base.if', typeVersion: 2.3, position,
  parameters: { conditions: { options: { caseSensitive: true, leftValue: '', typeValidation: 'strict', version: 2 },
    conditions: [{ id: `match-${kind}`, leftValue: '={{ $("Dispatch").item.json.body.kind }}',
      rightValue: kind, operator: { type: 'string', operation: 'equals' } }], combinator: 'and' }, options: {} },
});
const fail = { id: 'invalid-job', name: 'Unsupported job kind', type: 'n8n-nodes-base.stopAndError',
  typeVersion: 1, position: [620, 420], parameters: { errorMessage: 'Unsupported job kind' } };
const link = (name) => [{ node: name, type: 'main', index: 0 }];
function save(name, id, nodes, connections) {
  writeFileSync(join(output, `${name}.json`), `${JSON.stringify({ id, name: `AI Lead | ${name}`,
    active: false, nodes, connections, settings, pinData: {}, tags: [] }, null, 2)}\n`);
}
save('lead-processing', 'aiLeadProcessing', [webhook('ai-lead-processing'),
  isKind('lead_processing', 'Lead processing?', [220, 0]),
  httpStep('Analyze', 'analyze', [440, -80]), httpStep('Draft', 'draft', [660, -80]),
  httpStep('Finish', 'finish', [880, -80]), fail], {
  Dispatch: { main: [link('Lead processing?')] },
  'Lead processing?': { main: [link('Analyze'), link('Unsupported job kind')] },
  Analyze: { main: [link('Draft')] }, Draft: { main: [link('Finish')] },
});
save('communication', 'aiLeadCommunication', [webhook('ai-lead-communication'),
  isKind('notification', 'Notification?', [220, 0]), httpStep('Notify', 'notify', [660, -200]),
  isKind('email_send', 'Email send?', [440, 100]), httpStep('Send approved message', 'send', [660, 0]),
  isKind('followup_prepare', 'Follow-up draft?', [660, 200]),
  httpStep('Prepare follow-up draft', 'draft', [880, 180]),
  httpStep('Finish', 'finish', [1100, -80]), { ...fail, position: [880, 400] }], {
  Dispatch: { main: [link('Notification?')] },
  'Notification?': { main: [link('Notify'), link('Email send?')] },
  'Email send?': { main: [link('Send approved message'), link('Follow-up draft?')] },
  'Follow-up draft?': { main: [link('Prepare follow-up draft'), link('Unsupported job kind')] },
  Notify: { main: [link('Finish')] }, 'Send approved message': { main: [link('Finish')] },
  'Prepare follow-up draft': { main: [link('Finish')] },
});
function schedule(name, id, minutes, route) {
  const trigger = { id: 'schedule', name: 'Schedule', type: 'n8n-nodes-base.scheduleTrigger',
    typeVersion: 1.3, position: [0, 0], parameters: { rule: { interval: [{ field: 'minutes', minutesInterval: minutes }] } } };
  const operation = { id: 'operation', name: 'Run backend check', type: 'n8n-nodes-base.httpRequest',
    typeVersion: 4.4, position: [240, 0], parameters: { method: 'POST', url: `${baseUrl}${route}`,
      authentication: 'genericCredentialType', genericAuthType: 'httpHeaderAuth',
      options: { timeout: 90000 } }, credentials: internal, retryOnFail: false };
  save(name, id, [trigger, operation], { Schedule: { main: [link('Run backend check')] } });
}
schedule('mail-sync', 'aiLeadMailSync', 1, '/internal/v1/mailboxes/default/sync');
schedule('followup-check', 'aiLeadFollowupCheck', 5, '/internal/v1/followups/check');
console.log('Generated four workflows without credentials or personal data.');
