import { Rows } from 'lucide-react';
import { RecordCollectionWidget } from '../../components/views/RecordCollectionWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'operations-ui/record-collection',
  component: RecordCollectionWidget,
  meta: { label: 'Record Collection', icon: Rows, description: 'Configurable related records as tables or responsive cards' },
  source: 'plugin',
  scope: 'entry',
};

export default manifest;
