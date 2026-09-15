import { PanelTop } from 'lucide-react';

import { ModalRegionWidget } from '../../components/views/ModalRegionWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'modal_region',
  component: ModalRegionWidget,
  meta: {
    label: 'Modal Region',
    icon: PanelTop,
    description: 'A button that opens an Oracle-APEX-style modal page composed of nested regions',
  },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
