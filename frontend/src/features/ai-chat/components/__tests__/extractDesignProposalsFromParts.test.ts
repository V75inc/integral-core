import { describe, expect, it } from 'vitest';

import {
  extractDesignProposalsFromParts,
  hasDesignProposalFromParts,
} from '../extractDesignProposalsFromParts';

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

  it('pulls staged design_proposal cards for inline expansion', () => {
    const parts = [
      {
        type: 'tool-call',
        result: JSON.stringify({
          _kind: 'staged_change',
          kind: 'design_proposal',
          token: 't-design',
          summary: 'Assets app',
          diff_human: '**Assets** track with name, category, status.',
        }),
      },
    ];
    const out = extractDesignProposalsFromParts(undefined, parts);
    expect(out).toHaveLength(1);
    expect(out[0].summary).toBe('Assets app');
    expect(out[0].proposal).toContain('Assets');
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

  it('identifies a structured proposal so its prose echo can be suppressed', () => {
    const parts = [
      {
        type: 'tool-call',
        result: JSON.stringify({
          _kind: 'staged_change',
          kind: 'design_proposal',
          summary: 'Assets app',
          proposal: '**Assets** track with name, category, status.',
        }),
      },
    ];

    expect(hasDesignProposalFromParts(undefined, parts)).toBe(true);
  });
});
