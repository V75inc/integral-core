import { describe, expect, it } from 'vitest';

import {
  isPromptSheetResume,
  parsePromptSheetResume,
  promptSheetResumeDisplay,
} from '../resumeDisplay';

describe('prompt sheet resume display', () => {
  it('parses a bullet list resume', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'Resolved prompts',
      '• Approved — File content as Salaries will be paid early',
      '• Approved — Delete entry Salaries will be paid early',
      'Please continue.',
    ].join('\n');
    expect(isPromptSheetResume(raw)).toBe(true);
    const view = parsePromptSheetResume(raw);
    expect(view.title).toBe('Resolved prompts');
    expect(view.items).toEqual([
      'Approved — File content as Salaries will be paid early',
      'Approved — Delete entry Salaries will be paid early',
    ]);
    expect(view.footer).toBe('Please continue.');
  });

  it('parses legacy semicolon paragraphs', () => {
    const raw =
      '[PROMPT_SHEET]\nResolved prompts: approved "A"; approved "B". Please continue.';
    const view = parsePromptSheetResume(raw);
    expect(view.title).toBe('Resolved prompts');
    expect(view.items.length).toBe(2);
    expect(view.footer).toBe('Please continue.');
  });

  it('leaves ordinary user text alone', () => {
    expect(isPromptSheetResume('hello')).toBe(false);
    expect(promptSheetResumeDisplay('hello')).toBe('hello');
  });
});
