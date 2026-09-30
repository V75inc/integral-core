import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ComputerUseSection } from '../ComputerUseSection';

describe('ComputerUseSection', () => {
  afterEach(() => {
    cleanup();
    delete (window as Window & { integralDesktop?: unknown }).integralDesktop;
  });

  it('turns selected local apps into a native-consent-gated approval request', async () => {
    const approveComputerUse = vi.fn(async () => ({
      ok: true,
      grantId: 'grant-1',
      expiresAt: '2026-09-30T13:00:00.000Z',
      runtimeGeneration: 1,
    }));
    Object.defineProperty(window, 'integralDesktop', {
      configurable: true,
      value: {
        isDesktop: true,
        getComputerUseConfig: () => ({
          bundled: true,
          active: false,
          expiresAt: null,
          bindingGeneration: 'binding-1',
        }),
        listComputerUseApps: async () => ({
          ok: true,
          apps: [{ pid: 42, name: 'Editor', bundleId: 'com.example.Editor' }],
        }),
        approveComputerUse,
        stopComputerUse: vi.fn(async () => ({ ok: true })),
      },
    });

    render(<ComputerUseSection />);
    fireEvent.click(screen.getByRole('button', { name: 'Choose applications' }));
    await screen.findByRole('checkbox', { name: 'Editor' });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Editor' }));
    fireEvent.click(screen.getByRole('checkbox', { name: /background clicks and typing/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve access' }));

    await waitFor(() =>
      expect(approveComputerUse).toHaveBeenCalledWith({
        apps: [{ pid: 42, name: 'Editor', bundleId: 'com.example.Editor' }],
        durationMinutes: 30,
        screenshotsAllowed: true,
        actionsAllowed: true,
      }),
    );
    expect(await screen.findByText('Active on this device')).toBeInTheDocument();
  });

  it('shows remembered approved apps and duration from the desktop host', () => {
    Object.defineProperty(window, 'integralDesktop', {
      configurable: true,
      value: {
        isDesktop: true,
        getComputerUseConfig: () => ({
          bundled: true,
          active: true,
          grantId: 'grant-1',
          expiresAt: '2026-09-30T13:00:00.000Z',
          durationMinutes: 60,
          screenshotsAllowed: true,
          actionsAllowed: true,
          apps: [{ name: 'Calculator', bundleId: 'com.apple.calculator' }],
          bindingGeneration: 'binding-1',
        }),
        listComputerUseApps: async () => ({ ok: true, apps: [] }),
        approveComputerUse: vi.fn(),
        stopComputerUse: vi.fn(async () => ({ ok: true })),
      },
    });

    render(<ComputerUseSection />);
    expect(screen.getByText('Calculator')).toBeInTheDocument();
    expect(screen.getByText(/Access lasts 1 hour and expires/)).toBeInTheDocument();
  });

  it('saves Jev enablement and a TypeSafe API key through the desktop host', async () => {
    const setComputerUseJev = vi.fn(async () => ({
      ok: true,
      jevEnabled: true,
      jevConfigured: true,
    }));
    Object.defineProperty(window, 'integralDesktop', {
      configurable: true,
      value: {
        isDesktop: true,
        getComputerUseConfig: () => ({
          bundled: true,
          active: false,
          jevEnabled: false,
          jevConfigured: false,
          bindingGeneration: 'binding-1',
        }),
        listComputerUseApps: async () => ({ ok: true, apps: [] }),
        approveComputerUse: vi.fn(),
        stopComputerUse: vi.fn(async () => ({ ok: true })),
        setComputerUseJev,
      },
    });

    render(<ComputerUseSection />);
    fireEvent.click(screen.getByRole('button', { name: /Enable Jev for native apps/ }));
    fireEvent.change(screen.getByLabelText('TypeSafe API key'), {
      target: { value: 'sk-typesafe-test' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Jev settings' }));

    await waitFor(() =>
      expect(setComputerUseJev).toHaveBeenCalledWith({
        enabled: true,
        apiKey: 'sk-typesafe-test',
      }),
    );
    expect(await screen.findByText('Jev on')).toBeInTheDocument();
  });
});
