/** Public form presentation is declared by the host, never inferred from domain names. */
export type PublicFormStep = { id: string; title: string; fields: any[]; readonly?: boolean; notice?: string };
const csv = (value: string | undefined) => (value || '').split(',').map(v => v.trim()).filter(Boolean);
export function buildPublicFormSteps(
  schema: { fields?: any[] } | undefined,
  titleEnabled: boolean,
  bodyEnabled: boolean,
  extensions: Record<string, string> = {},
  reviewEnabled = false,
  acknowledgmentsEnabled = false,
): PublicFormStep[] {
  const steps: PublicFormStep[] = [];
  const defaultGroup = extensions.form_default_group?.trim() || 'Details';
  const hiddenKeys = new Set(csv(extensions.form_hidden_field_keys));
  const hiddenPrefixes = csv(extensions.form_hidden_field_prefixes);
  const hiddenGroups = new Set(csv(extensions.form_hidden_groups).map(v => v.toLowerCase()));
  const readonlyGroups = new Set(csv(extensions.form_readonly_groups).map(v => v.toLowerCase()));
  const groups = new Map<string, any[]>();
  if (titleEnabled) groups.set(defaultGroup, [{ type: 'base_title' }]);
  for (const spec of schema?.fields || []) {
    const key = String(spec.key || '');
    const group = String(spec.group || '').trim() || defaultGroup;
    if (hiddenKeys.has(key) || hiddenPrefixes.some(prefix => key.startsWith(prefix)) || hiddenGroups.has(group.toLowerCase())) continue;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push({ type: 'custom', spec });
  }
  const appendGroup = (title: string, fields: any[]) => {
    const readonly = readonlyGroups.has(title.toLowerCase());
    steps.push({ id: `fields-${steps.length}`, title, fields, readonly,
      notice: readonly ? extensions.form_prefill_notice?.trim() : undefined });
  };
  if (groups.has(defaultGroup)) {
    appendGroup(defaultGroup, groups.get(defaultGroup)!);
    groups.delete(defaultGroup);
  }
  if (reviewEnabled) steps.push({ id: 'document-review', title: extensions.form_review_step_title?.trim() || 'Document Review', fields: [{ type: 'document_review' }] });
  for (const [title, fields] of groups) appendGroup(title, fields);
  if (bodyEnabled) steps.push({ id: 'additional-info', title: 'Additional Info', fields: [{ type: 'base_body' }] });
  if (acknowledgmentsEnabled) steps.push({ id: 'acknowledgment-documents', title: 'Documents', fields: [{ type: 'document_acknowledgments' }] });
  return steps;
}
export function isPublicFormStepReadOnly(step: Pick<PublicFormStep, 'readonly'>, locked: boolean): boolean {
  return locked || Boolean(step.readonly);
}
