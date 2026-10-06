/** Body text for staged cards: strip a leading summary already shown as title. */

export function diffBodyWithoutSummary(
  summary: string | null | undefined,
  diffHuman: string | null | undefined,
): string {
  const body = (diffHuman ?? "").trimStart();
  const title = (summary ?? "").trim();
  if (!body) return "";
  if (!title) return body;
  const firstLine = body.split(/\r?\n/, 1)[0];
  // Core's resource stagers use a quoted summary and a formatted heading.
  // Compare those exact composition forms, never arbitrary model paragraphs
  // or field values. Retained proposals get the same presentation as new ones.
  const resourceTitle = /^(.*?) [“"](.+)[”"](?: in .+)?$/.exec(title);
  const formattedTitle = resourceTitle
    ? `**${resourceTitle[1]}** *${resourceTitle[2]}*`
    : null;
  const renderedHeading = /^\*\*([^*]+)\*\* (?:\*([^*]+)\*|`([^`]+)`)$/.exec(firstLine);
  const plainHeading = renderedHeading
    ? `${renderedHeading[1]} ${renderedHeading[2] ?? renderedHeading[3]}`
    : null;
  if (firstLine === title || firstLine === formattedTitle || plainHeading === title) {
    return body.slice(firstLine.length).replace(/^(?:\r?\n)+/, "");
  }
  return body;
}
