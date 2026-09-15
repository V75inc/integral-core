import { Grid3x3 } from 'lucide-react';

import { ComposableGrid } from '../../components/views/composable/ComposableGrid';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'composable_grid',
  component: ComposableGrid,
  meta: {
    label: 'Composable grid',
    icon: Grid3x3,
    description: 'Card grid driven by group_by / color_by / projection / card_layout.',
  },
  source: 'builtin',
};

export default manifest;

