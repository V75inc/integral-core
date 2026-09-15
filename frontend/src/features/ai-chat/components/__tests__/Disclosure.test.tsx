/**
 * B-AGENT-01 regression — reasoning is collapsed by default at rest.
 *
 * The hand-rolled `<Disclosure>` this file used to guard has been RETIRED
 * (rejected design, twice). Integral's reasoning / tool sections are now the
 * jvchat-faithful assistant-ui scaffold (`Reasoning` / `ToolGroup` /
 * `ToolFallback`), and the per-exchange reasoning section is `ReasoningChain`
 * (defined inline in `Thread.tsx`, built on the ported `Reasoning*` primitives).
 *
 * The ORIGINAL B-AGENT-01 intent — "reasoning starts collapsed at rest" — is
 * preserved by `ReasoningChain`, which:
 *   - is rendered for the `group-reasoning` grouped part in `Thread.tsx`, and
 *   - opens only while the message is running (`useState(running)` seeds it
 *     false at rest; the effect calls `setOpen(true)` while running and
 *     `setOpen(false)` on finish), so when the turn is NOT running it is closed;
 *   - sits on `ReasoningRoot`, whose ported collapsible keeps jvchat's
 *     `defaultOpen = false`.
 *
 * The previous component-mount cases (which imported the now-removed
 * `<Disclosure>` directly) are gone because their subject no longer exists. The
 * source-shape guards below assert the same intent against the new wiring.
 */
import { describe, it, expect } from 'vitest';
import threadSource from '../Thread.tsx?raw';
import reasoningSource from '../Reasoning.tsx?raw';

describe('Thread.tsx — B-AGENT-01 guard (reasoning collapsed at rest)', () => {
  it('routes the group-reasoning part through <ReasoningChain', () => {
    // The grouped reasoning part must render via ReasoningChain (the section
    // that owns the collapsed-at-rest behavior), not an always-open renderer.
    const match = threadSource.match(
      /case "group-reasoning":[\s\S]*?<ReasoningChain/,
    );
    expect(
      match,
      'group-reasoning case does not render <ReasoningChain',
    ).toBeTruthy();
  });

  it('ReasoningChain seeds its open state from `running` (closed at rest)', () => {
    // `useState(running)` seeds open from the run status — false at rest.
    const seedsFromRunning = threadSource.match(
      /function ReasoningChain[\s\S]*?useState\(running\)/,
    );
    expect(
      seedsFromRunning,
      'ReasoningChain does not seed open state from `running`',
    ).toBeTruthy();
  });

  it('ReasoningChain auto-collapses when the turn finishes', () => {
    // The effect closes the section once it transitions out of running.
    const collapsesOnFinish = threadSource.match(
      /function ReasoningChain[\s\S]*?wasRunning\.current\)\s*setOpen\(false\)/,
    );
    expect(
      collapsesOnFinish,
      'ReasoningChain does not auto-collapse when the turn finishes',
    ).toBeTruthy();
  });

  it('ReasoningRoot defaults to closed (defaultOpen = false)', () => {
    // The ported collapsible root keeps jvchat's `defaultOpen = false`, so an
    // uncontrolled reasoning section is closed until explicitly opened.
    expect(
      reasoningSource.match(/defaultOpen = false/),
      'ReasoningRoot does not default to closed',
    ).toBeTruthy();
  });
});
