import { describe, expect, it } from 'vitest';
import { buildPublicFormSteps, isPublicFormStepReadOnly } from './publicFormSteps';
const hostPolicy = {
  form_default_group: 'Personal Details', form_readonly_groups: 'Personal Details',
  form_hidden_field_keys: 'employee,status,submitted_at,policy_acknowledgments',
  form_hidden_field_prefixes: 'contract_', form_hidden_groups: 'Review',
  form_prefill_notice: 'Host-provided prefill notice', form_review_step_title: 'Employment Contract',
};
describe('host-declared public form presentation', () => {
  it('keeps synthetic applicant fields and title editable without domain steps', () => {
    const fields = ['company', 'contact_name', 'email', 'business_activity'].map(key => ({ key, type: 'text' }));
    const steps = buildPublicFormSteps({ fields }, true, true);
    expect(steps.map(s => s.title)).toEqual(['Details', 'Additional Info']);
    expect(steps[0].fields[0].type).toBe('base_title');
    expect(steps[0].fields.slice(1).map(f => f.spec.key)).toEqual(fields.map(f => f.key));
    expect(steps.every(s => !isPublicFormStepReadOnly(s, false))).toBe(true);
    expect(steps.every(s => !s.notice)).toBe(true);
  });
  it('preserves host exclusions and prefilled readonly fields explicitly', () => {
    const fields = ['company', 'employee', 'status', 'submitted_at', 'policy_acknowledgments', 'contract_reference'].map(key => ({ key }));
    fields.push({ key: 'internal_note', group: 'Review' } as any);
    const steps = buildPublicFormSteps({ fields }, true, true, hostPolicy, true, true);
    expect(steps.map(s => s.title)).toEqual(['Personal Details', 'Employment Contract', 'Additional Info', 'Documents']);
    expect(steps[0].fields.slice(1).map(f => f.spec.key)).toEqual(['company']);
    expect(isPublicFormStepReadOnly(steps[0], false)).toBe(true);
    expect(steps[0].notice).toBe(hostPolicy.form_prefill_notice);
    expect(isPublicFormStepReadOnly(steps[2], false)).toBe(false);
  });
  it('does not infer policy from field or group names', () => {
    const spec = { key: 'status', group: 'Personal Details' };
    const steps = buildPublicFormSteps({ fields: [spec] }, false, false);
    expect(steps[0].fields[0].spec).toBe(spec);
    expect(isPublicFormStepReadOnly(steps[0], false)).toBe(false);
  });
  it('retains explicit schema readonly and approved-entry locks', () => {
    const spec = { key: 'reference', readonly: true };
    const steps = buildPublicFormSteps({ fields: [spec] }, false, false);
    expect(steps[0].fields[0].spec.readonly).toBe(true);
    expect(isPublicFormStepReadOnly(steps[0], true)).toBe(true);
  });
  it('uses neutral document review when no host title exists', () => {
    expect(buildPublicFormSteps({}, false, false, {}, true)[0].title).toBe('Document Review');
  });
});
