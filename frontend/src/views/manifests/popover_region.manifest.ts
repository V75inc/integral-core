import { MessageSquare } from 'lucide-react';

import { PopoverRegionWidget } from '../../components/views/PopoverRegionWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'popover_region',
  component: PopoverRegionWidget,
  meta: {
    label: 'Popover Region',
    icon: MessageSquare,
    description: 'A button that opens a small floating panel anchored to it, for quick peeks',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
