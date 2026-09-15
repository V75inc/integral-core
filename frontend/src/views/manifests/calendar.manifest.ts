import { Calendar as CalendarIcon } from 'lucide-react';

import { CalendarWidget } from '../../components/views/CalendarWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'calendar',
  component: CalendarWidget,
  meta: {
    label: 'Calendar',
    icon: CalendarIcon,
    description: 'Time-based planning with month, week, and day views',
  },
};

export default manifest;

