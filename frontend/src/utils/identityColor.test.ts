import { describe, expect, it } from 'vitest';
import { resolveIdentityColor } from './index';

describe('resolveIdentityColor', () => {
  it('inherits the workspace color when an item has no explicit color', () => {
    expect(resolveIdentityColor('', ' #6633cc ')).toBe('#6633cc');
  });

  it('preserves an explicit item color over the workspace color', () => {
    expect(resolveIdentityColor(' #ff8844 ', '#6633cc')).toBe('#ff8844');
  });

  it('leaves unconfigured items on the theme default', () => {
    expect(resolveIdentityColor('', '')).toBeUndefined();
  });
});
