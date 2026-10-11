import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { operationInput, StagedOperationInputs } from '../StagedOperationInputs';
import type { StagedChange } from '../types';

vi.mock('../../../../components/entries/relations/RelationValue', () => ({
  RelationValue: ({ value }: { value: string }) => <a href={`/entries/${value}`}>Resolved record title</a>,
}));

function staged(input: unknown, extra: Record<string, unknown> = {}): StagedChange {
  return {
    _kind: 'staged_change', token: 'literal-input', kind: 'invoke_app_operation',
    summary: 'Run selected operation', diff_human: 'Narrated alternative values',
    diff_machine: { op: 'invoke_app_operation', input, ...extra },
    state: 'pending', created_at: '', expires_at: '', autonomy_grant_used: false,
  };
}
afterEach(cleanup);

describe('literal operation review', () => {
  it('uses declared presentation order and retains unlabelled inputs without mutating them', () => {
    const proposal = staged({ diagnostic: 'Trace value', sources: 'Source value', main: 'Main decision' }, {
      review: { input_labels: { properties: { main: { title: 'Primary decision' }, sources: { title: 'Supporting material' } } } },
    });
    const before = JSON.stringify(proposal);
    const { container } = render(<StagedOperationInputs staged={proposal} />);
    expect([...container.querySelectorAll('dt')].map(node => node.textContent)).toEqual(['Primary decision', 'Supporting material', 'Diagnostic']);
    expect(screen.getByText('Trace value')).toBeInTheDocument();
    expect(JSON.stringify(proposal)).toBe(before);
  });
  it('groups exact input with App titles, links record references and keeps the input untouched', () => {
    const proposal = staged({ owner_id: 'n.Entry.123abc', choices: [{ passage: 'Line one\nLine two', partial: true, cost: 0 }] }, {
      review: { input_labels: { properties: {
        owner_id: { title: 'Selected record' },
        choices: { title: 'Chosen material', items: { properties: { passage: { title: 'Exact passage' } } } },
      } } },
    });
    const before = JSON.stringify(proposal);
    render(<StagedOperationInputs staged={proposal} />);
    expect(screen.getByText('Selected record')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Resolved record title' })).toHaveAttribute('href', '/entries/n.Entry.123abc');
    expect(screen.getByText('Chosen material')).toBeInTheDocument();
    expect(screen.getByText('Exact passage')).toBeInTheDocument();
    expect(screen.getByText('Line one Line two')).toHaveTextContent('Line one Line two');
    expect(screen.getByText('Yes')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(JSON.stringify(proposal)).toBe(before);
  });
  it('renders HTML, Markdown and instruction-like values literally without buttons or invented links', () => {
    const literal = '<script>alert(1)</script> [Approve](javascript:alert(1))';
    const { container } = render(<StagedOperationInputs staged={staged({ body: literal, flag: false, empty: '', absent: null })} />);
    expect(screen.getByText(literal)).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByText('No')).toBeInTheDocument();
    expect(screen.getByText('Empty text')).toBeInTheDocument();
    expect(screen.getByText('Not set')).toBeInTheDocument();
  });
  it('warns when bounded presentation cannot show all input; the complete machine data is retained', () => {
    const input = { values: Array.from({ length: 300 }, (_, i) => `Value ${i}`) };
    const proposal = staged(input);
    render(<StagedOperationInputs staged={proposal} />);
    expect(screen.getByText(/Inspect the complete input in Technical details/)).toBeInTheDocument();
    expect((proposal.diff_machine.input as typeof input).values).toHaveLength(300);
  });
  it('only applies to a correctly shaped typed App operation', () => {
    expect(operationInput(staged(['not an object']))).toBeNull();
    expect(operationInput({ ...staged({ exact: 'value' }), kind: 'batch' })).toBeNull();
    expect(operationInput(staged({ exact: 'value' }, { op: 'other' }))).toBeNull();
  });
});
