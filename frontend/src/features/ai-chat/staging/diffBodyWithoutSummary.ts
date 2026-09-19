/** Body text for staged cards: strip a leading summary already shown as title. */

export function diffBodyWithoutSummary(
  summary: string | null | undefined,
  diffHuman: string | null | undefined,
): string {
  const body = (diffHuman ?? "").trimStart();
  const title = (summary ?? "").trim();
  if (!body) return "";
  if (!title) return body;
  if (body === title) return "";
  if (body.startsWith(title)) {
    const rest = body.slice(title.length).replace(/^\r?\n+/, "");
    return rest;
  }
  return body;
}
