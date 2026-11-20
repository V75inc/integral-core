/** Presentation only. Hiding another-response does not prevent duplicate submissions. */
export function formConfirmation(raw: unknown, assigned: boolean) {
  const c = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const text = (v: unknown) => typeof v === 'string' ? v.trim() : '';
  return {
    title: text(c.title) || 'Response Recorded',
    message: text(c.message) || (assigned
      ? 'Thank you! Your form has been submitted.'
      : 'Thank you! Your response has been submitted successfully to the track.'),
    allowAnotherResponse: c.allow_another_response !== false,
  };
}
