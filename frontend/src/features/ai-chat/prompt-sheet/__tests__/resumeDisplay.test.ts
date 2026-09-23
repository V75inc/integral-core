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

  it('hides host continuation instructions from the visible transcript', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'Prompt resolved',
      '• Approved — Create entry "2025 Prius" in Cars',
      '<!-- INTEGRAL_AGENT_DIRECTIVE',
      'Read the record before continuing. Do not repeat the write.',
      '-->',
    ].join('\n');
    expect(parsePromptSheetResume(raw)).toEqual({
      title: 'Prompt resolved',
      items: ['Approved — Create entry "2025 Prius" in Cars'],
      footer: null,
    });
  });

  it('hides the legacy unbounded continuation instruction', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'Prompt resolved',
      '• Approved — Create entry "2025 Prius" in Cars',
      'The approved writes above have already been applied. Do not repeat the write.',
    ].join('\n');
    expect(parsePromptSheetResume(raw)).toEqual({
      title: 'Prompt resolved',
      items: ['Approved — Create entry "2025 Prius" in Cars'],
      footer: null,
    });
  });

  it('leaves ordinary user text alone', () => {
    expect(isPromptSheetResume('hello')).toBe(false);
    expect(promptSheetResumeDisplay('hello')).toBe('hello');
  });
});
