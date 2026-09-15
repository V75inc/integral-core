import { FileBarChart } from 'lucide-react';
import { ReportCenterWidget } from '../../components/views/ReportCenterWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'operations-ui/report-center',
  component: ReportCenterWidget,
  meta: { label: 'Report Center', icon: FileBarChart, description: 'Configurable operational reports with metrics' },
  source: 'plugin',
  scope: 'track',
};

export default manifest;
