/**
 * Inline card for ``integral_propose_design`` results.
 *
 * The model often puts the track/field expansion only in collapsed reasoning
 * and a one-line closer in the text channel. The tool now carries the full
 * ``proposal`` body; this card surfaces it outside the tool-call fold.
 */

import ReactMarkdown from 'react-markdown';

import { Surface, Text } from '../../../ui';

type Props = {
  summary?: string;
  proposal: string;
};

export function DesignProposalCard({ summary, proposal }: Props) {
  return (
    <Surface tone="panel" border="subtle" radius="card" padding="md">
      <Text as="div" variant="label" tone="muted" className="mb-1.5 uppercase tracking-wide">
        Proposed design
      </Text>
      {summary ? (
        <Text as="div" variant="body-sm" tone="muted" className="mb-2">
          {summary}
        </Text>
      ) : null}
      <div className="prose prose-sm max-w-none dark:prose-invert [&_p]:my-1.5 [&_ul]:my-1.5 [&_li]:my-0.5">
        <ReactMarkdown>{proposal}</ReactMarkdown>
      </div>
      <Text as="div" variant="body-sm" tone="muted" className="mt-2">
        Confirm, correct fields, or say what to change — then I&apos;ll stage the
        build.
      </Text>
    </Surface>
  );
}
