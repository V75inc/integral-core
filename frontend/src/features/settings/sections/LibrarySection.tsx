/**
 * Settings → Content Profiles — slim explainer.
 *
 * The full catalog lives at /content-profiles. This panel shows a brief
 * description and a count of installed profiles, then points the user
 * to the canonical catalog page.
 */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';

import { contentProfilesApi } from '../../../api/contentProfiles';
import { SettingsSection } from '../components/Field';
import { Text } from '../../../ui';

const LIBRARY_QUERY_KEY = ['library', 'list'] as const;

export function LibrarySection() {
  const { data } = useQuery({
    queryKey: LIBRARY_QUERY_KEY,
    queryFn: () => contentProfilesApi.list(),
  });

  const packages = useMemo(() => data ?? [], [data]);
  const platformCount = useMemo(
    () => packages.filter(cp => cp.scope === 'platform').length,
    [packages],
  );
  const totalCount = packages.length;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Content Profiles
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Declarative bundles that define Apps and Track customizations —
          entry types, views, taxonomy, skills, agents, and settings.
        </Text>
      </div>

      <SettingsSection
        title="Installed profiles"
        description="The catalog page lets you browse every available profile and import new ones."
      >
        {totalCount > 0 && (
          <Text variant="body" tone="muted" as="p">
            {totalCount} {totalCount === 1 ? 'profile' : 'profiles'} installed
            {platformCount > 0 && (
              <>, {platformCount} platform-scoped</>
            )}
            .
          </Text>
        )}

        <div>
          <Link
            to="/content-profiles"
            className="
              inline-flex items-center gap-1.5
              rounded-[var(--radius-input)]
              bg-[var(--cta-bg)] hover:bg-[var(--cta-hover)]
              text-[var(--cta-fg)]
              px-3 py-1.5 text-sm font-medium
              shadow-sm
              transition-colors duration-fast
            "
          >
            Browse catalog
            <ArrowRight size={14} strokeWidth={1.5} />
          </Link>
        </div>
      </SettingsSection>
    </div>
  );
}
