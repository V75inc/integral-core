import { LayoutGrid } from 'lucide-react';

import { ComposableBoard } from '../../components/views/composable/ComposableBoard';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'composable_board',
  component: ComposableBoard,
  meta: {
    label: 'Composable board',
    icon: LayoutGrid,
    description:
      'Kanban-style board driven by group_by / color_by / swimlanes / sort_within_column.',
  },
  source: 'builtin',
};

export default manifest;

