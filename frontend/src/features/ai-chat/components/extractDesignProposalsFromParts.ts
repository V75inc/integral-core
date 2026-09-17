import { coerceToolResult } from '../staging/StagedChangeToolUI';

type ToolishPart = { type?: string; result?: unknown; isError?: boolean };

export type DesignProposal = {
  key: string;
  summary: string;
  proposal: string;
};

/**
 * Scan assistant tool-call parts for ``_kind: "design_proposal"`` results
 * (``integral_propose_design``) so the expansion can render outside the
 * collapsed tool group — the fix for "thinks it proposed but user sees nothing".
 */
export function extractDesignProposalsFromParts(
  content: ReadonlyArray<ToolishPart> | undefined,
  parts: ReadonlyArray<ToolishPart> | undefined,
): DesignProposal[] {
  const all = [
    ...((content as ReadonlyArray<ToolishPart> | undefined) ?? []),
    ...((parts as ReadonlyArray<ToolishPart> | undefined) ?? []),
  ];
  const out: DesignProposal[] = [];
  const seen = new Set<string>();
  for (const part of all) {
    if (part?.type !== 'tool-call' || part.isError) continue;
    const coerced = coerceToolResult(part.result) as
      | {
          _kind?: string;
          summary?: string;
          proposal?: string;
        }
      | null;
    if (!coerced || coerced._kind !== 'design_proposal') continue;
    const proposal = (coerced.proposal || '').trim();
    if (!proposal) continue;
    const summary = (coerced.summary || '').trim();
    const key = `${summary}::${proposal.slice(0, 64)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ key, summary, proposal });
  }
  return out;
}
