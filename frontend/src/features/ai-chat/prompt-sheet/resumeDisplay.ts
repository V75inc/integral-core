/** Detect / parse Prompt Sheet resume turns in the transcript. */

export const PROMPT_SHEET_RESUME_MARKER = '[PROMPT_SHEET]';

export function isPromptSheetResume(text: string): boolean {
  return text.trimStart().startsWith(PROMPT_SHEET_RESUME_MARKER);
}

export interface PromptSheetResumeView {
  title: string;
  items: string[];
  footer: string | null;
}

/** Structured view for the quiet system note (marker stripped). */
export function parsePromptSheetResume(text: string): PromptSheetResumeView {
  const trimmed = text.trimStart();
  const body = trimmed.startsWith(PROMPT_SHEET_RESUME_MARKER)
    ? trimmed.slice(PROMPT_SHEET_RESUME_MARKER.length).replace(/^\n+/, '').trim()
    : text.trim();

  const lines = body
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean);

  if (lines.length === 0) {
    return { title: body, items: [], footer: null };
  }

  const items: string[] = [];
  let footer: string | null = null;
  let title = lines[0];

  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    if (/^•\s+/.test(line) || /^[-*]\s+/.test(line)) {
      items.push(line.replace(/^([•\-*]\s+)/, ''));
      continue;
    }
    if (/^please continue\.?$/i.test(line)) {
      footer = line.replace(/\.$/, '') + '.';
      continue;
    }
    // Legacy single-paragraph format — split on "; " if present.
    if (i === 1 && items.length === 0 && /;\s+/.test(line)) {
      const parts = line.split(/;\s+/).map((p) => p.replace(/\.$/, '').trim());
      items.push(...parts.filter(Boolean));
      continue;
    }
    items.push(line);
  }

  // Legacy: "Resolved prompts: a; b. Please continue." on one/two lines.
  if (items.length === 0 && /:\s+/.test(title)) {
    const [head, rest] = title.split(/:\s+/, 2);
    title = head.trim();
    const chunk = (rest || '').replace(/\s*Please continue\.?$/i, '').trim();
    if (chunk) {
      items.push(
        ...chunk
          .split(/;\s+/)
          .map((p) => p.replace(/\.$/, '').trim())
          .filter(Boolean),
      );
    }
    if (/please continue/i.test(rest || '') || /please continue/i.test(body)) {
      footer = 'Please continue.';
    }
  }

  if (!footer && /please continue\.?$/i.test(body)) {
    footer = 'Please continue.';
  }

  return { title, items, footer };
}

/** @deprecated prefer parsePromptSheetResume for list rendering */
export function promptSheetResumeDisplay(text: string): string {
  const view = parsePromptSheetResume(text);
  const parts = [view.title, ...view.items.map((i) => `• ${i}`)];
  if (view.footer) parts.push(view.footer);
  return parts.join('\n');
}
