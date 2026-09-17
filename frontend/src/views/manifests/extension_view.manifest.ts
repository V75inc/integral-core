import { AppWindow } from 'lucide-react';
import { ExtensionViewWidget } from '../../components/views/ExtensionViewWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'extension_view',
  component: ExtensionViewWidget,
  meta: {
    label: 'App extension',
    icon: AppWindow,
    description: 'Sandboxed iframe view from an installed app package',
  },
  scope: 'both',
  source: 'builtin',
};

export default manifest;
