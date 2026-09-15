import { HeartPulse } from 'lucide-react';
import { DataHealthWidget } from '../../components/views/DataHealthWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = { type: 'operations-ui/data-health', component: DataHealthWidget, meta: { label: 'Data Health', icon: HeartPulse, description: 'Configurable completeness checks for any record' }, source: 'plugin', scope: 'both' };
export default manifest;
