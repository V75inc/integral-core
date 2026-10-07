import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, Link } from 'react-router-dom';
import { waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthPageLayout } from '../AuthPageLayout';

describe('AuthPageLayout', () => {
  it('restarts the split-square ripple when navigating between auth routes', async () => {
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route
            path="/login"
            element={
              <AuthPageLayout title="Sign in" description="Welcome back.">
                <Link to="/signup">Create an account</Link>
              </AuthPageLayout>
            }
          />
          <Route
            path="/signup"
            element={
              <AuthPageLayout title="Create an account" description="Join us.">
                <Link to="/login">Sign in</Link>
              </AuthPageLayout>
            }
          />
        </Routes>
      </MemoryRouter>,
    );

    const initialRipple = screen
      .getByRole('heading', { name: 'Sign in' })
      .closest('main')
      ?.querySelector('svg[data-animation-cycle]');
    fireEvent.click(screen.getByRole('link', { name: 'Create an account' }));

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Create an account' })).toBeInTheDocument(),
    );
    const nextRipple = screen
      .getByRole('heading', { name: 'Create an account' })
      .closest('main')
      ?.querySelector('svg[data-animation-cycle]');
    expect(nextRipple).not.toBe(initialRipple);
    expect(nextRipple?.querySelectorAll('.auth-ripple-mark:not(.auth-ripple-mark--base)'))
      .toHaveLength(3);
    expect(nextRipple?.querySelectorAll('.auth-ripple-mark--base')).toHaveLength(1);
    expect(nextRipple?.querySelector('.auth-ripple-mark--base')?.querySelectorAll('path')).toHaveLength(2);
    expect(nextRipple?.querySelector('rect')).toBeNull();
  });

  it('restarts the split-square ripple after a back-forward cache restore', () => {
    render(
      <MemoryRouter>
        <AuthPageLayout title="Welcome" description="Your space is ready.">
          <button type="button">Continue</button>
        </AuthPageLayout>
      </MemoryRouter>,
    );

    const ripple = screen.getByRole('heading', { name: 'Welcome' })
      .closest('main')
      ?.querySelector('svg[data-animation-cycle]');
    expect(ripple).toHaveAttribute('data-animation-cycle', '1');
    expect(ripple?.querySelectorAll('.auth-ripple-mark:not(.auth-ripple-mark--base)'))
      .toHaveLength(3);
    expect(ripple?.querySelectorAll('.auth-ripple-mark--base')).toHaveLength(1);

    const restoredEvent = new Event('pageshow');
    Object.defineProperty(restoredEvent, 'persisted', { value: true });
    act(() => window.dispatchEvent(restoredEvent));

    expect(
      screen
        .getByRole('heading', { name: 'Welcome' })
        .closest('main')
        ?.querySelector('svg[data-animation-cycle]'),
    ).toHaveAttribute('data-animation-cycle', '2');
  });
});
