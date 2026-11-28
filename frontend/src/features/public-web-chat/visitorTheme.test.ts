import { expect, it } from 'vitest';
import { parseVisitorTheme } from './visitorTheme';

it('accepts supported themes and defaults invalid or missing values to system', () => {
  expect(['light', 'dark', 'system', 'unknown', null].map(parseVisitorTheme))
    .toEqual(['light', 'dark', 'system', 'system', 'system']);
});
