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
  // Require an EOL after the title so a same-line prefix cannot truncate copy
  // (e.g. summary "Create track" must not strip "Create track Clients").
  if (body.startsWith(`${title}\n`) || body.startsWith(`${title}\r\n`)) {
    return body.slice(title.length).replace(/^\r?\n+/, "");
  }
  return body;
}
