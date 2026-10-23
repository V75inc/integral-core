/** Native Home authoring: reusable functions produce data, never an iframe. */
export const NATIVE_HOME_COMPONENTS = ['stack', 'grid', 'card', 'heading', 'text', 'markdown', 'metric', 'chart', 'records', 'form', 'action', 'tabs', 'accordion'] as const;
export interface HomeInput { name: string; label: string; type?: 'text' | 'textarea' | 'number' | 'email' | 'checkbox'; required?: boolean }
export interface HomeElement {
  id: string;
  type: typeof NATIVE_HOME_COMPONENTS[number];
  title?: string; text?: string; children?: HomeElement[]; columns?: 1 | 2 | 3 | 4; gap?: 'sm' | 'md' | 'lg';
  query_key?: string; params?: Record<string, unknown>; value_path?: string; rows_path?: string;
  label_field?: string; value_field?: string; chart_type?: 'bar' | 'line' | 'pie';
  fields?: HomeInput[]; operation_key?: string; payload?: Record<string, unknown>; submit_label?: string;
}
export interface NativeHome { title: string; description?: string; renderer: 'native'; content: HomeElement }
/** Compose your own reusable components using the native palette and typed props. */
export function defineComponent<P>(factory: (props: P) => HomeElement): (props: P) => HomeElement { return factory; }
export function defineHome(home: NativeHome): NativeHome {
  const ids = new Set<string>(); let count = 0;
  const visit = (node: HomeElement, depth: number) => {
    if (!node.id || ids.has(node.id) || ++count > 128 || depth > 12 || !(NATIVE_HOME_COMPONENTS as readonly string[]).includes(node.type)) throw new Error('Invalid or unbounded native Home composition.');
    ids.add(node.id); (node.children || []).forEach(child => visit(child, depth + 1));
  };
  visit(home.content, 0);
  if (new TextEncoder().encode(JSON.stringify(home)).length > 131072) throw new Error('Home exceeds 128 KiB.');
  return home;
}
