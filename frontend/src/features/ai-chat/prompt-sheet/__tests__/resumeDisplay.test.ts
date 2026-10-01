import { describe, expect, it } from 'vitest';

import {
  isPromptSheetResume,
  parsePromptSheetResume,
  promptSheetResumeDisplay,
} from '../resumeDisplay';
import { resumeIfNeeded } from '../usePromptQueue';

describe('prompt sheet resume display', () => {
  it('renders the resolved review before sending its continuation prompt', () => {
    const appended: Array<{ role: string; content: Array<{ type: string; text: string }> }> = [];
    const runtime = {
      append: (message: (typeof appended)[number]) => appended.push(message),
    };
    const review = '[PROMPT_SHEET]\nUpdates applied\n• Draft diff (not published): added views: Table';

    resumeIfNeeded(runtime as never, review);

    expect(appended.map((message) => message.role)).toEqual([
      'assistant',
      'user',
    ]);
    expect(appended[0].content[0].text).toBe(review);
    expect(appended[1].content[0].text).toBe(review);
  });

  it('parses a natural residual bullet list', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'You confirmed a few changes',
      '• File content as Salaries will be paid early',
      '• Delete entry Salaries will be paid early',
    ].join('\n');
    expect(isPromptSheetResume(raw)).toBe(true);
    const view = parsePromptSheetResume(raw);
    expect(view.title).toBe('You confirmed a few changes');
    expect(view.items).toEqual([
      'File content as Salaries will be paid early',
      'Delete entry Salaries will be paid early',
    ]);
    expect(view.footer).toBeNull();
  });

  it('softens robotic legacy titles and Approved bullets', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'Prompt resolved',
      '• Approved — Create entry "2025 Prius" in Cars',
    ].join('\n');
    expect(parsePromptSheetResume(raw)).toEqual({
      title: 'Updates applied',
      items: ['Create entry "2025 Prius" in Cars'],
      footer: null,
    });
  });

  it('parses legacy semicolon paragraphs', () => {
    const raw =
      '[PROMPT_SHEET]\nResolved prompts: approved "A"; approved "B". Please continue.';
    const view = parsePromptSheetResume(raw);
    expect(view.title).toBe('You confirmed a few changes');
    expect(view.items.length).toBe(2);
    expect(view.footer).toBeNull();
  });

  it('hides host continuation instructions from the visible transcript', () => {
    const raw = [
      '[PROMPT_SHEET]',
      'Updates applied',
      '• Create entry "2025 Prius" in Cars',
      '<!-- INTEGRAL_AGENT_DIRECTIVE',
      'Read the record before continuing. Do not repeat the write.',
      '-->',
    ].join('\n');
    expect(parsePromptSheetResume(raw)).toEqual({
      title: 'Updates applied',
      items: ['Create entry "2025 Prius" in Cars'],
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
      title: 'Updates applied',
      items: ['Create entry "2025 Prius" in Cars'],
      footer: null,
    });
  });

  it('leaves ordinary user text alone', () => {
    expect(isPromptSheetResume('hello')).toBe(false);
    expect(promptSheetResumeDisplay('hello')).toBe('hello');
  });
});
