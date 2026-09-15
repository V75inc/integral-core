import { FileText } from 'lucide-react';

import { StaticContentWidget } from '../../components/views/StaticContentWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'static_content',
  component: StaticContentWidget,
  meta: {
    label: 'Static Content',
    icon: FileText,
    description: 'Read-only markdown block for section instructions, headers, or notes',
  },
  source: 'plugin',
  // Used both as a standalone track view and referenced from
  // related_views[] — see the backend ViewTypeSpec's own scope="both"
  // comment (region_system/__init__.py).
  scope: 'both',
};

export default manifest;
