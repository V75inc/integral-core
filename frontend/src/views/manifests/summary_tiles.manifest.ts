import { Gauge } from 'lucide-react';

import { SummaryTilesWidget } from '../../components/views/SummaryTilesWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'summary_tiles',
  component: SummaryTilesWidget,
  meta: {
    label: 'Summary Tiles',
    icon: Gauge,
    description: 'Read-only KPI strip reading values off the bound entry',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
