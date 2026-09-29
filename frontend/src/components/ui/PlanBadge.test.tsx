import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import {
  PlanBadge,
  normalizePlanKey,
  planLabelForKey,
} from './PlanBadge';

describe('PlanBadge helpers', () => {
  it('normalizes legacy base and empty keys', () => {
    expect(normalizePlanKey(null)).toBe('free');
    expect(normalizePlanKey('')).toBe('free');
    expect(normalizePlanKey('base')).toBe('basic');
    expect(normalizePlanKey('Premium')).toBe('premium');
  });

  it('labels known plan keys', () => {
    expect(planLabelForKey(null)).toBe('Free');
    expect(planLabelForKey('basic')).toBe('Basic');
    expect(planLabelForKey('base')).toBe('Basic');
    expect(planLabelForKey('premium')).toBe('Premium');
  });
});

describe('PlanBadge', () => {
  it('renders Free by default', () => {
    render(<PlanBadge />);
    expect(screen.getByText('Free')).toBeInTheDocument();
  });

  it('renders plan_label when provided', () => {
    render(<PlanBadge plan_key="premium" plan_label="Premium" />);
    expect(screen.getByText('Premium')).toBeInTheDocument();
  });

  it('sets title when canceling', () => {
    render(
      <PlanBadge
        plan_key="basic"
        plan_label="Basic"
        cancel_at_period_end
      />,
    );
    expect(screen.getByTitle('Canceling at period end')).toBeInTheDocument();
  });

  it('sets title when past due', () => {
    render(
      <PlanBadge
        plan_key="basic"
        plan_label="Basic"
        subscription_status="past_due"
      />,
    );
    expect(screen.getByTitle('Past due')).toBeInTheDocument();
  });
});
