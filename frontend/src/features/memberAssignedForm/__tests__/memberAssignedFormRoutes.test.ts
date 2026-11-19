import { describe, expect, it } from 'vitest';

import {
  MEMBER_ASSIGNED_FORM_PATH,
  isMemberAssignedFormPathname,
  resolveMemberAssignedFormNavigateTarget,
} from '../memberAssignedFormRoutes';

describe('resolveMemberAssignedFormNavigateTarget', () => {
  it('maps legacy public share URLs to member route with entry', () => {
    const legacy =
      'http://localhost:9006/public/tracks/tok123?entry=n.Entry.abc';
    expect(resolveMemberAssignedFormNavigateTarget(legacy)).toBe(
      `${MEMBER_ASSIGNED_FORM_PATH}?entry=n.Entry.abc`,
    );
  });

  it('preserves canonical assigned entry route', () => {
    expect(
      resolveMemberAssignedFormNavigateTarget(
        `${MEMBER_ASSIGNED_FORM_PATH}?entry=n.Entry.xyz`,
      ),
    ).toBe(`${MEMBER_ASSIGNED_FORM_PATH}?entry=n.Entry.xyz`);
  });
});

describe('isMemberAssignedFormPathname', () => {
  it('matches only the generic route', () => {
    expect(isMemberAssignedFormPathname('/me/assigned-form')).toBe(true);
    expect(isMemberAssignedFormPathname('/custom-app/assignment')).toBe(false);
  });
});
