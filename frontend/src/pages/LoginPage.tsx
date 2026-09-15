import { useState } from 'react';
import { Link, useNavigate, useLocation, useSearchParams } from 'react-router-dom';
import { Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { Button, LINE_ICON_STROKE, Logo } from '../components/ui';
import { LOGIN_FOOTER, LOGIN_TAGLINE } from '../brand';
import { safePostAuthRedirect } from '../utils';
import { validateLogin, type FieldErrors } from '../utils/authValidation';

/**
 * LoginPage — Quiet Premium auth surface.
 *
 * Two-column at md+, single-column at sm: editorial left (brand + lede)
 * and form right. Inter throughout, weight 600 display title, no
 * bordered card around the form. The Logo primitive provides the brand
 * presentation (mark + wordmark) shared with the in-app sidebar so the
 * mark is consistent end-to-end.
 */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const sessionExpired = searchParams.get('reason') === 'session_expired';
  const accountDeleted = Boolean(
    (location.state as { accountDeleted?: boolean } | null)?.accountDeleted,
  );
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<FieldErrors<'email' | 'password' | 'form'>>({});

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const fieldErrs = validateLogin({ email, password });
    if (Object.keys(fieldErrs).length > 0) {
      setErrors(fieldErrs);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      await login(email, password);
      const from = (location.state as { from?: typeof location } | null)?.from;
      navigate(safePostAuthRedirect(from), { replace: true });
    } catch (err: unknown) {
      // Single error channel: inline only (B-AUTH-02 — the previous
      // toast + inline pair was redundant). Backend's friendliest
      // message wins; fall back to a clean default.
      const detail = (
        err as { response?: { data?: { detail?: string } } }
      )?.response?.data?.detail;
      setErrors({
        form:
          typeof detail === 'string' && detail.trim()
            ? detail
            : 'Invalid credentials.',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--bg)] flex items-stretch">
      {/* Left column — editorial brand block (md+ only).
          Center-aligned composition: logo, hero, footer all anchored to
          the column's center axis. Twin diagonal accent washes + ripple
          ring ornament fill the negative space. */}
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
          <h1 className="text-[64px] xl:text-[76px] font-semibold tracking-[-0.04em] text-[var(--text)] leading-[0.96]">
            Welcome back.
          </h1>
          <p className="mt-6 text-[19px] text-[var(--text-muted)] leading-snug max-w-[440px] mx-auto">
            {LOGIN_TAGLINE}
          </p>
        </div>
        <p className="text-xs text-[var(--text-subtle)]">{LOGIN_FOOTER}</p>
      </section>

      {/* Right column — form. */}
      <section className="flex-1 flex flex-col justify-center px-6 md:px-16 py-12">
        <div className="w-full max-w-sm mx-auto">
          {/* Mobile-only logo — desktop logo lives in the editorial column. */}
          <div className="md:hidden mb-10">
            <Logo to="/" size="md" />
          </div>

          <h2 className="text-[32px] font-semibold tracking-[-0.02em] text-[var(--text)] leading-[1.1]">
            Sign in
          </h2>
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            Sign in to continue.
          </p>

          {accountDeleted ? (
            <p
              className="mt-4 rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text-muted)]"
              role="status"
            >
              Your account has been permanently deleted.
            </p>
          ) : null}

          {sessionExpired && !accountDeleted ? (
            <p
              className="mt-4 rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text-muted)]"
              role="status"
            >
              Your session expired. Sign in again to continue.
            </p>
          ) : null}

          <form onSubmit={handleSubmit} className="mt-8 space-y-5">
            <div>
              <label
                htmlFor="login-email"
                className="text-[13px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] block mb-2"
              >
                Email
              </label>
              <input
                id="login-email"
                type="email"
                value={email}
                onChange={e => {
                  setEmail(e.target.value);
                  if (errors.email) setErrors(prev => ({ ...prev, email: undefined }));
                }}
                autoFocus
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={Boolean(errors.email)}
                aria-describedby={errors.email ? 'login-email-error' : undefined}
                className={`
                  w-full px-3.5 py-2.5 text-sm
                  rounded-[var(--radius-input)]
                  bg-[var(--panel)] border ${errors.email ? 'border-[var(--danger-fg)]' : 'border-[var(--panel-border)]'}
                  text-[var(--text)] placeholder:text-[var(--text-subtle)]
                  hover:border-[var(--text-subtle)]
                  focus:outline-none focus:border-[var(--text-muted)] focus:bg-[var(--panel-2)]
                  transition-colors duration-fast
                `}
              />
              {errors.email ? (
                <p
                  id="login-email-error"
                  role="alert"
                  className="mt-1.5 text-xs text-[var(--danger-fg)]"
                >
                  {errors.email}
                </p>
              ) : null}
            </div>
            <div>
              <label
                htmlFor="login-password"
                className="text-[13px] font-medium uppercase tracking-[0.08em] text-[var(--text-subtle)] block mb-2"
              >
                Password
              </label>
              <div className="relative">
                <input
                  id="login-password"
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={e => {
                    setPassword(e.target.value);
                    if (errors.password) setErrors(prev => ({ ...prev, password: undefined }));
                  }}
                  autoComplete="current-password"
                  placeholder="••••••••"
                  aria-invalid={Boolean(errors.password)}
                  aria-describedby={errors.password ? 'login-password-error' : undefined}
                  className={`
                    w-full pr-11 pl-3.5 py-2.5 text-sm
                    rounded-[var(--radius-input)]
                    bg-[var(--panel)] border ${errors.password ? 'border-[var(--danger-fg)]' : 'border-[var(--panel-border)]'}
                    text-[var(--text)] placeholder:text-[var(--text-subtle)]
                    hover:border-[var(--text-subtle)]
                    focus:outline-none focus:border-[var(--text-muted)] focus:bg-[var(--panel-2)]
                    transition-colors duration-fast
                  `}
                />
                <button
                  type="button"
                  onClick={() => setShowPw(s => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors duration-fast"
                  aria-label={showPw ? 'Hide password' : 'Show password'}
                >
                  {showPw ? (
                    <EyeOff size={16} strokeWidth={LINE_ICON_STROKE} />
                  ) : (
                    <Eye size={16} strokeWidth={LINE_ICON_STROKE} />
                  )}
                </button>
              </div>
              {errors.password ? (
                <p
                  id="login-password-error"
                  role="alert"
                  className="mt-1.5 text-xs text-[var(--danger-fg)]"
                >
                  {errors.password}
                </p>
              ) : null}
            </div>
            {errors.form ? (
              <p
                role="alert"
                className="text-xs text-[var(--danger-fg)] -mt-2"
              >
                {errors.form}
              </p>
            ) : null}
            <Button
              variant="primary"
              size="md"
              loading={loading}
              className="w-full mt-1"
            >
              Sign in
            </Button>
          </form>

          <div className="mt-6 flex flex-col gap-2 text-sm text-[var(--text-muted)]">
            <Link
              to="/forgot-password"
              className="hover:text-[var(--text)] transition-colors duration-fast"
            >
              Forgot your password?
            </Link>
            <span>
              New here?{' '}
              <Link
                to="/signup"
                state={location.state}
                className="text-[var(--text)] underline hover:text-[var(--link-hover)]"
              >
                Create an account
              </Link>
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}
