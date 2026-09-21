import { describe, expect, it } from 'vitest';
import { diffBodyWithoutSummary } from '../diffBodyWithoutSummary';

describe('diffBodyWithoutSummary', () => {
  it('strips a leading summary line duplicated into the body', () => {
    const summary =
      'Set up Car Rental Manager app with cars, renters, rental status, and service/document reminders.';
    const body = `${summary}\n- Author library Operational Model "Car Rental Manager"`;
    expect(diffBodyWithoutSummary(summary, body)).toBe(
      '- Author library Operational Model "Car Rental Manager"',
    );
  });

  it('returns empty when body is only the summary', () => {
    expect(diffBodyWithoutSummary('Hello', 'Hello')).toBe('');
  });

  it('leaves unrelated body alone', () => {
    expect(diffBodyWithoutSummary('Title', '- step one')).toBe('- step one');
  });

  it('does not strip when title is only a same-line prefix', () => {
    expect(diffBodyWithoutSummary('Create track', 'Create track Clients')).toBe(
      'Create track Clients',
    );
  });
});
