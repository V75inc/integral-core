import { Table2 } from 'lucide-react';

import { EditableTableWidget } from '../../components/views/EditableTableWidget';
import type { WidgetRegistration } from '../types';

const manifest: WidgetRegistration = {
  type: 'editable_table',
  component: EditableTableWidget,
  meta: {
    label: 'Editable Table',
    icon: Table2,
    description: 'Spreadsheet-style inline-editable grid — add/edit/remove rows in place',
  },
  source: 'plugin',
  // 'both': addressable as a normal tab on its own anchored line track,
  // AND embedded via :anchored_track/lines_table inside a host entry's
  // related_views[] (every NIS Schedule / PAYE Filing / Form 2 / Form 7B
  // entry does this). Matches
  // app/plugins/payroll_filings/__init__.py's backend scope.
  scope: 'both',
};

export default manifest;
