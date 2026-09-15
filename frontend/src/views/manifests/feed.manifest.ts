import { LayoutList } from 'lucide-react';

import { FeedWidget } from '../../components/views/FeedWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'feed',
  component: FeedWidget,
  meta: {
    label: 'Feed',
    icon: LayoutList,
    description: 'Reverse-chronological activity stream',
  },
};

export default manifest;

