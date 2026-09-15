import { useState } from 'react';
import { Link } from 'react-router-dom';
import { authApi } from '../api/auth';
import { Button, Logo } from '../components/ui';
import { LOGIN_FOOTER, LOGIN_TAGLINE } from '../brand';

const FIELD_INPUT_CLASSES = `
  w-full px-3.5 py-2.5 text-sm
  rounded-[var(--radius-input)]
  bg-[var(--panel)] border border-[var(--panel-border)]
  text-[var(--text)] placeholder:text-[var(--text-subtle)]
  hover:border-[var(--text-subtle)]
  focus:outline-none focus:border-[var(--text-muted)] focus:bg-[var(--panel-2)]
  transition-colors duration-fast
`;

const FIELD_LABEL_CLASSES =
  'text-[13px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] block mb-2';

/**
 * Step 1 of password recovery: user enters their email; we trigger a reset.
 *
 * The backend returns the same generic 200 response regardless of whether
 * an account exists for that address (anti-enumeration), so this page
 * always shows the same confirmation message after submit.
 */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.forgotPassword(email);
    } catch {
      // Anti-enumeration: same UX regardless of network/server outcome.
    } finally {
      setLoading(false);
      setSubmitted(true);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--bg)] flex items-stretch">
      {/* Left column — editorial brand block (md+ only). Same composition
          as Login/Signup: centered axis, ripple ornament, no logo (logo
          lives above the form on the right). */}
      <section className="hidden md:flex flex-col items-center w-[56%] px-12 py-12 border-r border-[var(--panel-border)] relative isolate overflow-hidden text-center">
        <span className="auth-pattern" aria-hidden>
          <svg className="auth-ripple" viewBox="0 0 100 100" aria-hidden>
            <circle className="auth-ripple-circle" cx="50" cy="50" r="48" />
            <circle className="auth-ripple-circle" cx="50" cy="50" r="48" />
            <circle className="auth-ripple-circle" cx="50" cy="50" r="48" />
          </svg>
        </span>
        <Logo to="/" size="md" className="self-start relative z-10" />
        <div className="flex-1 flex flex-col justify-center max-w-[560px]">
          <h1 className="text-[60px] xl:text-[72px] font-semibold tracking-[-0.04em] text-[var(--text)] leading-[0.98]">
            Reset your password.
          </h1>
          <p className="mt-6 text-[19px] text-[var(--text-muted)] leading-snug max-w-[440px] mx-auto">
            {LOGIN_TAGLINE}
          </p>
        </div>
        <p className="text-xs text-[var(--text-subtle)]">{LOGIN_FOOTER}</p>
      </section>

      <section className="flex-1 flex flex-col justify-center px-6 md:px-16 py-12">
        <div className="w-full max-w-sm mx-auto">
          {/* Mobile-only logo — desktop logo lives in the editorial column. */}
          <div className="md:hidden mb-10">
            <Logo to="/" size="md" />
          </div>

          {submitted ? (
            <>
              <h2 className="text-[32px] font-semibold tracking-[-0.02em] text-[var(--text)] leading-[1.1]">
                Check your inbox
              </h2>
              <p className="mt-3 text-sm text-[var(--text-muted)] leading-relaxed">
                If an account exists for{' '}
                <span className="text-[var(--text)]">{email}</span>, we just
                sent a reset link. It expires in 60 minutes and works only once.
                Don't see it? Check your spam folder.
              </p>
              <div className="mt-8 flex flex-col gap-2 text-sm text-[var(--text-muted)]">
                <Link
                  to="/login"
                  className="hover:text-[var(--text)] transition-colors duration-fast"
                >
                  ← Back to sign in
                </Link>
                <button
                  type="button"
                  onClick={() => {
                    setSubmitted(false);
                    setEmail('');
                  }}
                  className="text-left hover:text-[var(--text)] transition-colors duration-fast"
                >
                  Use a different email
                </button>
              </div>
            </>
          ) : (
            <>
              <h2 className="text-[32px] font-semibold tracking-[-0.02em] text-[var(--text)] leading-[1.1]">
                Forgot your password?
              </h2>
              <p className="mt-2 text-sm text-[var(--text-muted)]">
                Enter the email on your account and we'll send you a link to set
                a new one.
              </p>

              <form onSubmit={handleSubmit} className="mt-8 space-y-5">
                <div>
                  <label htmlFor="forgot-email" className={FIELD_LABEL_CLASSES}>
                    Email
                  </label>
                  <input
                    id="forgot-email"
                    type="email"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    required
                    autoFocus
                    autoComplete="email"
                    placeholder="you@example.com"
                    className={FIELD_INPUT_CLASSES}
                  />
                </div>
                <Button
                  variant="primary"
                  size="md"
                  loading={loading}
                  className="w-full mt-1"
                >
                  Send reset link
                </Button>
              </form>

              <p className="mt-6 text-sm text-[var(--text-muted)]">
                <Link
                  to="/login"
                  className="hover:text-[var(--text)] transition-colors duration-fast"
                >
                  ← Back to sign in
                </Link>
              </p>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
