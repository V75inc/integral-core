import { Puzzle } from 'lucide-react';
import { EmptyState, IconWell, LINE_ICON_STROKE } from '../ui';

interface ExtensionViewFallbackProps {
  title?: string;
  message?: string;
}

export function ExtensionViewFallback({
  title = 'Extension view unavailable',
  message = 'The app extension view could not be loaded. Showing the standard record view instead.',
}: ExtensionViewFallbackProps) {
  return (
    <EmptyState
      icon={
        <IconWell size="lg" aria-hidden>
          <Puzzle size={22} strokeWidth={LINE_ICON_STROKE} />
        </IconWell>
      }
      title={title}
      description={message}
    />
  );
}
