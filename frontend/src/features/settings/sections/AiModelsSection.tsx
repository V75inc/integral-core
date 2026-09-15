/**
 * Settings tab — user AI model and API key configuration.
 */
import { Text } from '../../../ui';
import { ModelCredentialsSection } from './ModelCredentialsSection';

export function AiModelsSection() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          AI Models
        </Text>
        <Text variant="body" tone="muted" as="p" className="mt-1">
          Connect your AI provider and choose which models to use for chat,
          quick replies, harder problems, and images.
        </Text>
      </div>
      <ModelCredentialsSection />
    </div>
  );
}
