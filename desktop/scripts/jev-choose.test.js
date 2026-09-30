'use strict';

const assert = require('node:assert/strict');
const { test } = require('node:test');

const {
  TYPESAFE_SYSTEM_ONE_URL,
  buildNativeCandidates,
  chooseWithTypesafe,
} = require('../src/jev-choose');

test('native candidates keep AX labels and drop risky or unlabeled controls', () => {
  const built = buildNativeCandidates([
    { role: 'AXButton', label: '7', elementToken: 'tok-7', enabled: true, onScreen: true },
    { role: 'AXButton', label: 'Delete', elementToken: 'tok-del', enabled: true },
    { role: 'AXGroup', label: 'Window', elementToken: 'tok-win' },
    { role: 'AXTextField', label: 'Display', elementToken: 'tok-field', value: '' },
  ], { text: '42' });

  assert.deepEqual(
    built.candidates.map((item) => item.id),
    ['button.7', 'text_input.display', 'reobserve', 'abstain'],
  );
  assert.equal(built.resolve.get('button.7').elementToken, 'tok-7');
  assert.equal(built.resolve.get('text_input.display').action, 'type_text');
  assert.equal(built.resolve.get('text_input.display').text, '42');
  assert.ok(!JSON.stringify(built.candidates).includes('tok-'));
});

test('TypeSafe chooser posts a bounded choice and accepts only offered IDs', async () => {
  const requests = [];
  const result = await chooseWithTypesafe(
    {
      apiKey: 'sk-test',
      goal: 'Click 7',
      snapshotId: 'snapshot-1',
      captureId: 'capture-1',
      candidates: [
        { id: 'button.7', description: 'Click the button 7', source: 'ax' },
        { id: 'reobserve', description: 'Reobserve' },
        { id: 'abstain', description: 'Abstain' },
      ],
      elements: [{ role_class: 'button', label: '7', state: 'enabled' }],
      history: [],
    },
    {
      fetchImpl: async (url, init) => {
        requests.push({ url, init });
        return {
          ok: true,
          json: async () => ({
            model: 'jev-latest',
            choices: {
              candidate: {
                choice: 'button.7',
                confidence: 0.9,
                probabilities: { 'button.7': 0.9, reobserve: 0.1, abstain: 0 },
              },
            },
          }),
        };
      },
    },
  );

  assert.equal(requests[0].url, TYPESAFE_SYSTEM_ONE_URL);
  const body = JSON.parse(requests[0].init.body);
  assert.equal(body.questions.candidate.type, 'choice');
  assert.ok(!JSON.stringify(body).includes('tok-'));
  assert.equal(result.selectedId, 'button.7');
  assert.equal(result.confidence, 0.9);
});
