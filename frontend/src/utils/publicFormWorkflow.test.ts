import { describe, expect, it } from 'vitest';
import { publicFormWorkflow, workflowLocksEntry, workflowSubmissionFields } from './publicFormWorkflow';
describe('neutral declared form workflow', () => {
  it('does not infer policy from domain-shaped field names', () => {
    const config = publicFormWorkflow();
    expect(workflowLocksEntry(config, { status: 'approved', contract_status: 'rejected' })).toBe(false);
    expect(workflowSubmissionFields(config)).toEqual({});
    expect(config.reviewEnabled).toBe(false);
  });
  it('applies independently configured decision and submission vocabulary', () => {
    const config = publicFormWorkflow({ form_submit_field:'phase', form_submit_value:'received', form_lock_field:'phase', form_lock_values:'closed,archived', form_review_enabled:'true', form_review_status_field:'consent', form_review_accept_value:'yes', form_review_reject_value:'no' });
    expect(workflowSubmissionFields(config)).toEqual({ phase:'received' });
    expect(workflowLocksEntry(config,{phase:'CLOSED'})).toBe(true);
    expect(workflowLocksEntry(config,{status:'approved'})).toBe(false);
  });
  it('fails closed for incomplete declared workflows', () => {
    expect(()=>publicFormWorkflow({form_review_enabled:'true'})).toThrow();
    expect(()=>publicFormWorkflow({form_review_enabled:'yes'})).toThrow();
    expect(()=>publicFormWorkflow({form_acknowledgments_enabled:'1'})).toThrow();
    expect(()=>publicFormWorkflow({form_submit_field:'phase'})).toThrow();
    expect(()=>publicFormWorkflow({form_acknowledgments_enabled:'true'})).toThrow();
  });
});
