import { BookOpen } from 'lucide-react';

import { WikiWidget } from '../../components/views/WikiWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'wiki',
  component: WikiWidget,
  meta: {
    label: 'Wiki',
    icon: BookOpen,
    description: 'Hierarchical pages with sidebar tree and markdown reader',
  },
};

export default manifest;
