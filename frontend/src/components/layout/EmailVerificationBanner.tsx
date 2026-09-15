/**
 * EmailVerificationBanner — pushes a system notification when the
 * authenticated user has not verified their email yet.
 *
 * Non-dismissible by design: verification is the only way out, so the bar
 * stays pinned (with a "Resend code" / "enter your code" action) until the
 * server flips ``email_verified`` to true. Users can still take action
 * inline without losing the surface.
 *
 * All visual presentation is owned by <SystemNotificationBar>; this file
 * only models when to push and when to clear.
 */

import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { MailCheck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { authApi } from '../../api';
import { useSystemNotifications } from '../system';

const NOTIF_ID = 'auth:email-verification';

export function EmailVerificationBanner() {
  const { user } = useAuth();
  const { showToast } = useToast();
  const navigate = useNavigate();
  const { notify, dismiss, update } = useSystemNotifications();
  const resendingRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => () => { mountedRef.current = false; }, []);

  // Show whenever the user is authenticated AND not yet verified. A missing
  // ``email_verified`` field (e.g. cached pre-feature user) is treated as
  // unverified so the nudge appears — getting it wrong by under-showing
  // would hide the only path back to verification.
  const shouldShow = !!user && user.email_verified !== true;

  useEffect(() => {
    if (!shouldShow) {
      dismiss(NOTIF_ID);
      return;
    }
    // Clear on unmount too — Layout unmounts on logout, which would
    // otherwise leave the pushed notification stuck on the public
    // /login surface.
    const cleanup = () => dismiss(NOTIF_ID);
    const handleResend = async () => {
      if (resendingRef.current) return;
      resendingRef.current = true;
      // Reflect busy state in the bar without re-pushing (preserves animation).
      update(NOTIF_ID, {
        actions: [
          { label: 'Resend code', onClick: handleResend, busy: true },
          { label: 'enter your code', onClick: () => navigate('/verify-email') },
        ],
      });
      try {
        await authApi.resendVerification();
        showToast('Verification code sent — check your inbox', 'success');
        navigate('/verify-email');
      } catch {
        showToast('Could not send code. Try again shortly.', 'error');
      } finally {
        resendingRef.current = false;
        if (mountedRef.current) {
          update(NOTIF_ID, {
            actions: [
              { label: 'Resend code', onClick: handleResend, busy: false },
              { label: 'enter your code', onClick: () => navigate('/verify-email') },
            ],
          });
        }
      }
    };
    notify({
      id: NOTIF_ID,
      type: 'info',
      icon: MailCheck,
      title: 'Verify your email',
      actions: [
        { label: 'Resend code', onClick: handleResend },
        { label: 'enter your code', onClick: () => navigate('/verify-email') },
      ],
      dismissible: false,
    });
    return cleanup;
  }, [shouldShow, notify, dismiss, update, navigate, showToast]);

  return null;
}
