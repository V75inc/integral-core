import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { fetchAuditLog } from '../../../api/auditLog';
import { AuditLogSection } from './AuditLogSection';

vi.mock('../../../api/auditLog', () => ({
  fetchAuditLog: vi.fn().mockResolvedValue({ events: [] }),
}));

it('puts the Audit log heading above the filters and preserves deep-link parameters', async () => {
  render(
    <MemoryRouter initialEntries={['/settings#audit-log?action=policy.deny']}>
      <QueryClientProvider client={new QueryClient()}>
        <AuditLogSection />
      </QueryClientProvider>
    </MemoryRouter>,
  );

  const header = screen.getByRole('heading', { name: 'Audit log', level: 2 }).closest('header');
  expect(header).toHaveTextContent(/Use filters to diagnose/);
  expect(header).not.toContainElement(screen.getByRole('textbox', { name: 'Action' }));
  expect(await screen.findByText('No audit events')).toBeVisible();
  expect(fetchAuditLog).toHaveBeenCalledWith(expect.objectContaining({ action: 'policy.deny' }));
});
