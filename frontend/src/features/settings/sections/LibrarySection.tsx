import { PageMeta } from '../../../components/ui';
/** Settings links to the single App catalog and reusable Track model tools. */
import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight } from 'lucide-react';

import { operationalModelsApi } from '../../../api/operationalModels';
import { SettingsSection } from '../components/Field';
import { Button, Skeleton } from '../../../components/ui';
import { Text } from '../../../ui';

const LIBRARY_QUERY_KEY = ['library', 'list'] as const;

export function LibrarySection() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: LIBRARY_QUERY_KEY,
    queryFn: () => operationalModelsApi.list(),
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
          Operational Models
        </Text>
        <PageMeta as="p">
          Reusable models and App Packages that define records, views,
          operational guidance, skills, agents, and settings.
        </PageMeta>
      </div>

      <SettingsSection
        title="Available models"
        description="Manage Apps is the library for discovering, inspecting, importing, and installing Apps."
      >
        {isError ? <div role="alert"><Text as="p" variant="body-sm" tone="danger">Could not load available models.</Text><Button variant="secondary" size="sm" onClick={() => void refetch()}>Try again</Button></div> : isLoading ? <Skeleton className="h-6 w-40" /> : totalCount > 0 ? (
          <Text variant="body" tone="muted" as="p">
            {totalCount} {totalCount === 1 ? 'model' : 'models'} available
            {platformCount > 0 && (
              <>, {platformCount} platform-scoped</>
            )}
            .
          </Text>
        ) : <Text as="p" variant="body-sm" tone="muted">No models available yet.</Text>}

        <div>
          <Link
            to="/apps?manage=1"
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
            Browse Apps
            <ArrowRight size={14} strokeWidth={1.5} />
          </Link>
          <Link to="/models"><Text variant="body-sm">Manage reusable Track models</Text></Link>
        </div>
      </SettingsSection>
    </div>
  );
}
