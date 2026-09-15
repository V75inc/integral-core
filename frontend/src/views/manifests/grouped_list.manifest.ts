import { FolderTree } from 'lucide-react';

import { GroupedListWidget } from '../../components/views/GroupedListWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'grouped-list/by-relation',
  component: GroupedListWidget,
  meta: {
    label: 'Grouped List',
    icon: FolderTree,
    description:
      'Entries grouped into sections by any field — resolves a relation-typed group field to its label (e.g. an employee’s name) rather than a raw id',
  },
  source: 'plugin',
  // Matches app/plugins/grouped_list/__init__.py's backend scope.
  scope: 'track',
};

export default manifest;
