import { PanelRight } from 'lucide-react';

import { DrawerRegionWidget } from '../../components/views/DrawerRegionWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'drawer_region',
  component: DrawerRegionWidget,
  meta: {
    label: 'Drawer Region',
    icon: PanelRight,
    description: 'A button that opens a side panel instead of a centered modal dialog',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
