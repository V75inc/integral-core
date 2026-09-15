import { LayoutGrid } from 'lucide-react';

import { FormRegionWidget } from '../../components/views/FormRegionWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'form_region',
  component: FormRegionWidget,
  meta: {
    label: 'Form',
    icon: LayoutGrid,
    description: 'Config-driven subset-of-fields form bound to self, a related entry, or an anchored track',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
