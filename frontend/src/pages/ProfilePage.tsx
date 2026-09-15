import { Mail, Edit2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useSetCrumbs } from '../context/CrumbsContext';
import { Button, LINE_ICON_STROKE, PageShell, PageSection } from '../components/ui';
import { AvatarUploadControl } from '../components/ui/AvatarUploadControl';
import { formatRelativeTime } from '../utils';
import { usePublishPageContext } from '../hooks/usePublishPageContext';

export function ProfilePage() {
  useSetCrumbs([{ label: 'Profile' }]);
  const { user, refreshUser } = useAuth();

  // Which page, not what is on it — this one is entirely identity fields.
  usePublishPageContext({ pageKind: 'profile' });
  const navigate = useNavigate();

  if (!user) return null;

  const bio = user.bio || (user.preferences?.bio as string) || '';

  return (
    <PageShell>
      <PageSection>
        {/* Editorial header — Avatar IS the identity marker (no brand-accent
            bar). Avatar + display name aligned on a center axis at desktop;
            stacks at mobile. Action cluster pinned right at desktop. */}
        <header className="mb-12">
          <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:gap-7">
            <AvatarUploadControl
              target={{ kind: 'user', id: user.id }}
              avatar={{
                name: user.display_name,
                size: 'xl',
                attachmentId: user.avatar_attachment_id,
                userId: user.id,
                version: user.updated_at,
                ringVariant: 'none'
              }}
              onUploaded={() => refreshUser()}
            />
            <div className="min-w-0 flex-1">
              <h1 className="text-[40px] sm:text-[44px] font-semibold tracking-[-0.035em] text-[var(--text)] leading-[1.05] sm:truncate break-words">
                {user.display_name}
              </h1>
              <div className="mt-2.5 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-[var(--text-subtle)]">
                <span className="inline-flex items-center gap-1.5">
                  <Mail size={13} strokeWidth={LINE_ICON_STROKE} aria-hidden />
                  {user.email}
                </span>
                <span aria-hidden>·</span>
                <span>Member since {formatRelativeTime(user.created_at)}</span>
              </div>
            </div>
            <div className="flex gap-2 shrink-0 self-start sm:self-center">
              <Button
                variant="outline"
                size="sm"
                icon={<Edit2 size={13} strokeWidth={LINE_ICON_STROKE} />}
                onClick={() => navigate('/settings#profile')}
              >
                Edit profile
              </Button>
            </div>
          </div>
        </header>
      </PageSection>

      <PageSection.Separator />

      <PageSection className="mt-8">
        {bio ? (
          <p className="text-base text-[var(--text-muted)] max-w-2xl leading-relaxed">
            {bio}
          </p>
        ) : null}
      </PageSection>
    </PageShell>
  );
}
