import { describe, expect, it } from 'vitest';

import { extractDesignProposalsFromParts } from '../extractDesignProposalsFromParts';

describe('extractDesignProposalsFromParts', () => {
  it('pulls design_proposal tool results for inline cards', () => {
    const parts = [
      {
        type: 'tool-call',
        result: JSON.stringify({
          _kind: 'design_proposal',
          summary: 'CRM app',
          proposal: '**Contacts** and **Deals** with email, stage, board view.',
        }),
      },
    ];
    const out = extractDesignProposalsFromParts(undefined, parts);
    expect(out).toHaveLength(1);
    expect(out[0].summary).toBe('CRM app');
    expect(out[0].proposal).toContain('Contacts');
  });

  it('ignores non-design tool results', () => {
    const parts = [
      {
        type: 'tool-call',
        result: JSON.stringify({ _kind: 'staged_change', token: 't1' }),
      },
    ];
    expect(extractDesignProposalsFromParts(undefined, parts)).toEqual([]);
  });
});
