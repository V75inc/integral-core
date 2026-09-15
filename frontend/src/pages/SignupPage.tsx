import { useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { Button, Logo } from '../components/ui';
import { SIGNUP_FOOTER, SIGNUP_HEADLINE, SIGNUP_SUBCOPY } from '../brand';
import { safePostAuthRedirect } from '../utils';
import {
  PASSWORD_STRENGTH_LABELS,
  passwordStrength,
  validateSignup,
  type FieldErrors,
} from '../utils/authValidation';

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

export function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [workspaceName, setWorkspaceName] = useState('');
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<
    FieldErrors<'displayName' | 'email' | 'password' | 'form'>
  >({});

  const pwStrength = passwordStrength(password);
  const pwStrengthLabel = PASSWORD_STRENGTH_LABELS[pwStrength];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const fieldErrs = validateSignup({ displayName, email, password });
    if (Object.keys(fieldErrs).length > 0) {
      setErrors(fieldErrs);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      await signup(
        email,
        password,
        displayName,
        workspaceName.trim() || undefined,
      );
      const from = (location.state as { from?: typeof location } | null)?.from;
      const returnPath = safePostAuthRedirect(from);
      const pendingInvite = returnPath.startsWith('/invitations/');
      navigate(
        pendingInvite ? returnPath : '/verify-email',
        { replace: true },
      );
    } catch (err: unknown) {
      const detail = (
        err as { response?: { data?: { detail?: string } } }
      )?.response?.data?.detail;
      setErrors({
        form:
          typeof detail === 'string' && detail.trim()
            ? detail
            : 'Signup failed. Please try again.',
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--bg)] flex items-stretch">
      {/* Left column — editorial brand block (md+ only). Centered axis to
          match LoginPage: hero text and footer share the section's vertical
          midline; ripple ornament radiates from center. Logo lives above
          the form on the right, not here. */}
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
            {SIGNUP_HEADLINE}
          </h1>
          <p className="mt-6 text-[19px] text-[var(--text-muted)] leading-snug max-w-[440px] mx-auto">
            {SIGNUP_SUBCOPY}
          </p>
        </div>
        <p className="text-xs text-[var(--text-subtle)]">{SIGNUP_FOOTER}</p>
      </section>

      <section className="flex-1 flex flex-col justify-center px-6 md:px-16 py-12">
        <div className="w-full max-w-sm mx-auto">
          {/* Mobile-only logo — desktop logo lives in the editorial column. */}
          <div className="md:hidden mb-10">
            <Logo to="/" size="md" />
          </div>

          <h2 className="text-[32px] font-semibold tracking-[-0.02em] text-[var(--text)] leading-[1.1]">
            Create your account
          </h2>
          <p className="mt-2 text-sm text-[var(--text-muted)]">
            Your workspace is created automatically. Optionally name your collaborative workspace below.
          </p>

          <form onSubmit={handleSubmit} className="mt-8 space-y-5">
            <div>
              <label htmlFor="signup-name" className={FIELD_LABEL_CLASSES}>
                Display name
              </label>
              <input
                id="signup-name"
                value={displayName}
                onChange={e => {
                  setDisplayName(e.target.value);
                  if (errors.displayName) setErrors(p => ({ ...p, displayName: undefined }));
                }}
                autoComplete="name"
                placeholder="Your name"
                aria-invalid={Boolean(errors.displayName)}
                aria-describedby={errors.displayName ? 'signup-name-error' : undefined}
                className={FIELD_INPUT_CLASSES + (errors.displayName ? ' !border-[var(--danger-fg)]' : '')}
              />
              {errors.displayName ? (
                <p id="signup-name-error" role="alert" className="mt-1.5 text-xs text-[var(--danger-fg)]">
                  {errors.displayName}
                </p>
              ) : null}
            </div>
            <div>
              <label htmlFor="signup-email" className={FIELD_LABEL_CLASSES}>
                Email
              </label>
              <input
                id="signup-email"
                type="email"
                value={email}
                onChange={e => {
                  setEmail(e.target.value);
                  if (errors.email) setErrors(p => ({ ...p, email: undefined }));
                }}
                autoComplete="email"
                placeholder="you@example.com"
                aria-invalid={Boolean(errors.email)}
                aria-describedby={errors.email ? 'signup-email-error' : undefined}
                className={FIELD_INPUT_CLASSES + (errors.email ? ' !border-[var(--danger-fg)]' : '')}
              />
              {errors.email ? (
                <p id="signup-email-error" role="alert" className="mt-1.5 text-xs text-[var(--danger-fg)]">
                  {errors.email}
                </p>
              ) : null}
            </div>
            <div>
              <label htmlFor="signup-password" className={FIELD_LABEL_CLASSES}>
                Password
              </label>
              <input
                id="signup-password"
                type="password"
                value={password}
                onChange={e => {
                  setPassword(e.target.value);
                  if (errors.password) setErrors(p => ({ ...p, password: undefined }));
                }}
                autoComplete="new-password"
                placeholder="At least 8 characters"
                aria-invalid={Boolean(errors.password)}
                aria-describedby={errors.password ? 'signup-password-error' : 'signup-password-strength'}
                className={FIELD_INPUT_CLASSES + (errors.password ? ' !border-[var(--danger-fg)]' : '')}
              />
              {password.length > 0 && !errors.password ? (
                <div id="signup-password-strength" className="mt-2 flex items-center gap-2">
                  <div className="flex-1 flex gap-1" aria-hidden>
                    {[0, 1, 2, 3].map(i => (
                      <span
                        key={i}
                        className={`h-1 flex-1 rounded-full ${
                          i < pwStrength
                            ? pwStrength <= 1
                              ? 'bg-[var(--danger-fg)]'
                              : pwStrength === 2
                                ? 'bg-[var(--warn-fg)]'
                                : 'bg-[var(--success-fg,#16a34a)]'
                            : 'bg-[var(--panel-border)]'
                        }`}
                      />
                    ))}
                  </div>
                  <span className="text-[11px] text-[var(--text-muted)] tabular-nums">
                    {pwStrengthLabel}
                  </span>
                </div>
              ) : null}
              {errors.password ? (
                <p id="signup-password-error" role="alert" className="mt-1.5 text-xs text-[var(--danger-fg)]">
                  {errors.password}
                </p>
              ) : null}
            </div>
            <div>
              <label htmlFor="signup-workspace" className={FIELD_LABEL_CLASSES}>
                Collaborative workspace (optional)
              </label>
              <input
                id="signup-workspace"
                value={workspaceName}
                onChange={e => setWorkspaceName(e.target.value)}
                autoComplete="organization"
                placeholder="Your company or team"
                className={FIELD_INPUT_CLASSES}
              />
              <p className="mt-1.5 text-xs text-[var(--text-subtle)]">
                If provided, we’ll create a company workspace alongside your
                personal one and make you the owner.
              </p>
            </div>
            {errors.form ? (
              <p role="alert" className="text-xs text-[var(--danger-fg)] -mt-2">
                {errors.form}
              </p>
            ) : null}
            <Button
              variant="primary"
              size="md"
              loading={loading}
              className="w-full mt-1"
            >
              Create account
            </Button>
          </form>

          <p className="mt-6 text-sm text-[var(--text-muted)]">
            Already have an account?{' '}
            <Link
              to="/login"
              state={location.state}
              className="text-[var(--text)] underline hover:text-[var(--link-hover)]"
            >
              Sign in
            </Link>
          </p>
        </div>
      </section>
    </div>
  );
}
