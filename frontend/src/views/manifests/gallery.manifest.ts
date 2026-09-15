import { LayoutGrid } from 'lucide-react';

import { GalleryWidget } from '../../components/views/GalleryWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'gallery',
  component: GalleryWidget,
  meta: {
    label: 'Gallery',
    icon: LayoutGrid,
    description: 'Visual content browsing with image grid and lightbox',
  },
};

export default manifest;

