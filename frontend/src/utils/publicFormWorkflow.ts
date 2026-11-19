/** Declarative form workflow owned by the publishing App. No field-name inference. */
export interface PublicFormWorkflow {
  submitField: string;
  submitValue: string;
  lockField: string;
  lockValues: string[];
  reviewEnabled: boolean;
  reviewField: string;
  acceptValue: string;
  rejectValue: string;
  acknowledgmentsEnabled: boolean;
  acknowledgmentsField: string;
}
export function publicFormWorkflow(ext: Record<string, string> = {}): PublicFormWorkflow {
  const value = (key: string) => (ext[key] || '').trim();
  for (const key of ['form_review_enabled', 'form_acknowledgments_enabled']) {
    if (value(key) && !['true', 'false'].includes(value(key))) {
      throw new Error('Invalid App form workflow flag');
    }
  }
  const result = {
    submitField: value('form_submit_field'), submitValue: value('form_submit_value'),
    lockField: value('form_lock_field'), lockValues: value('form_lock_values').split(',').map(v => v.trim()).filter(Boolean),
    reviewEnabled: value('form_review_enabled') === 'true', reviewField: value('form_review_status_field'),
    acceptValue: value('form_review_accept_value'), rejectValue: value('form_review_reject_value'),
    acknowledgmentsEnabled: value('form_acknowledgments_enabled') === 'true', acknowledgmentsField: value('form_acknowledgments_field'),
  };
  if (Boolean(result.submitField) !== Boolean(result.submitValue) ||
      Boolean(result.lockField) !== Boolean(result.lockValues.length) ||
      (result.reviewEnabled && (!result.reviewField || !result.acceptValue || !result.rejectValue || result.acceptValue === result.rejectValue)) ||
      (result.acknowledgmentsEnabled && !result.acknowledgmentsField)) {
    throw new Error('Incomplete App form workflow configuration');
  }
  return result;
}
export function workflowLocksEntry(workflow: PublicFormWorkflow, fields: Record<string, unknown> = {}): boolean {
  return Boolean(workflow.lockField && workflow.lockValues.some(value => value.toLowerCase() === String(fields[workflow.lockField] ?? '').toLowerCase()));
}
export function workflowSubmissionFields(workflow: PublicFormWorkflow): Record<string, string> {
  return workflow.submitField ? { [workflow.submitField]: workflow.submitValue } : {};
}
