/**
 * Thin Get Started section — destination for the first-login banner
 * (`OnboardingGetStartedBanner` → `/settings#get-started`).
 */
import { useNavigate } from 'react-router-dom';
import { MessageSquare, LayoutGrid, ListIcon, Bot } from 'lucide-react';

import { Button } from '../../../components/ui/Button';
import { LINE_ICON_STROKE } from '../../../components/ui';
import { Text } from '../../../ui';
import { SettingsSection } from '../components/Field';

interface GetStartedSectionProps {
  navigateToSection?: (id: string) => void;
}

export function GetStartedSection({
  navigateToSection,
}: GetStartedSectionProps = {}) {
  const navigate = useNavigate();

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Get started
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Integral is an ops layer on a pluggable harness. A few short steps
          to begin working with your coworker.
        </Text>
      </div>

      <SettingsSection
        title="Talk to your coworker"
        description="Open the harness chat to ask Integral to scaffold tracks, draft entries, or explain your workspace."
      >
        <Button
          type="button"
          variant="primary"
          size="sm"
          icon={<MessageSquare size={14} strokeWidth={LINE_ICON_STROKE} />}
          onClick={() => navigate('/agent')}
        >
          Open harness chat
        </Button>
      </SettingsSection>

      <SettingsSection
        title="Organize work"
        description="Create a track (≈ table) or install an app (≈ schema) so entries have a place to live."
      >
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={<ListIcon size={14} strokeWidth={LINE_ICON_STROKE} />}
            onClick={() => navigate('/tracks')}
          >
            Tracks
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={<LayoutGrid size={14} strokeWidth={LINE_ICON_STROKE} />}
            onClick={() => navigate('/apps')}
          >
            Apps
          </Button>
        </div>
      </SettingsSection>

      <SettingsSection
        title="Choose your agent"
        description="Integral routes through one active agent per workspace. Switch providers anytime."
      >
        <Button
          type="button"
          variant="secondary"
          size="sm"
          icon={<Bot size={14} strokeWidth={LINE_ICON_STROKE} />}
          onClick={() => navigateToSection?.('agents')}
        >
          Open Agent settings
        </Button>
      </SettingsSection>
    </div>
  );
}
