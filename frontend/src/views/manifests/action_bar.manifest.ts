import { Zap } from 'lucide-react';

import { ActionBarWidget } from '../../components/views/ActionBarWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'action_bar',
  component: ActionBarWidget,
  meta: {
    label: 'Action Bar',
    icon: Zap,
    description: 'Row of buttons that invoke workspace tools against the current entry',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
