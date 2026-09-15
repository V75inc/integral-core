import { useMemo } from 'react';
import { useScope } from '../../../context/ScopeContext';
import { useChatPageContext } from '../../../context/ChatPageFocusContext';

export interface Suggestion {
  label: string;
  text: string;
}

/**
 * Returns scope-aware suggestion chips for the assistant empty state.
 *
 * Tiers:
 *  - page-context (when pageKind is known): page-specific scaffold prompts
 *  - workspace-level (no sub-context): generic activity / search prompts
 *  - personal workspace: personal productivity prompts
 *  - org workspace: team-oriented prompts
 */
export function useScopedSuggestions(): Suggestion[] {
  const { activeWorkspace, isPersonal } = useScope();
  const { pageKind, focusedTrackId, focusedAppId } = useChatPageContext();

  return useMemo((): Suggestion[] => {
    const workspaceName = activeWorkspace?.name;
    const pageChips = pageContextSuggestions(pageKind, {
      focusedTrackId,
      focusedAppId,
      workspaceName,
    });
    if (pageChips.length > 0) {
      return pageChips;
    }

    if (!workspaceName) {
      // No workspace resolved yet — return generic fallbacks
      return [
        {
          label: 'What can you help me with?',
          text: 'What can you help me with in this workspace?',
        },
        {
          label: 'Summarize recent activity',
          text: 'Summarize recent activity across my tracks and entries.',
        },
        {
          label: 'Help me find something',
          text: "Help me find entries or content I've been working on.",
        },
      ];
    }

    if (isPersonal) {
      // Personal workspace — individual productivity prompts
      return [
        {
          label: `What's happening in ${workspaceName}?`,
          text: `Give me a summary of recent activity in my ${workspaceName} workspace.`,
        },
        {
          label: 'Help me organize my tracks',
          text: 'Can you help me organize my tracks and entries more effectively?',
        },
        {
          label: "Find entries I haven't updated recently",
          text: "Which entries in my workspace haven't been updated recently?",
        },
        {
          label: 'Suggest a content structure',
          text: 'Suggest a content profile structure for my current work.',
        },
      ];
    }

    // Organization workspace — team-oriented prompts
    return [
      {
        label: `What's happening in ${workspaceName}?`,
        text: `Give me a summary of recent team activity in the ${workspaceName} workspace.`,
      },
      {
        label: 'Summarize open items across tracks',
        text: `What are the open or in-progress items across tracks in ${workspaceName}?`,
      },
      {
        label: 'Help me draft an entry',
        text: 'Help me draft a new entry. What information should I capture?',
      },
      {
        label: 'Review our content structure',
        text: `Review the content structure in ${workspaceName} and suggest improvements.`,
      },
    ];
  }, [
    activeWorkspace?.name,
    isPersonal,
    pageKind,
    focusedTrackId,
    focusedAppId,
  ]);
}

function pageContextSuggestions(
  pageKind: string | null,
  ctx: {
    focusedTrackId: string | null;
    focusedAppId: string | null;
    workspaceName?: string;
  },
): Suggestion[] {
  if (!pageKind) return [];

  switch (pageKind) {
    case 'tracks_list':
      return [
        {
          label: 'Ask Integral to scaffold a track',
          text: 'Help me scaffold a new track for my current work. Suggest a structure and entry types.',
        },
        {
          label: 'Propose a track layout',
          text: 'What tracks should I create to organize this workspace effectively?',
        },
        {
          label: 'Summarize my tracks',
          text: ctx.workspaceName
            ? `Summarize the tracks in ${ctx.workspaceName} and suggest gaps.`
            : 'Summarize my tracks and suggest gaps.',
        },
      ];
    case 'feed':
      return [
        {
          label: 'Draft my next entry',
          text: 'Help me draft a new feed entry. Ask me what to capture.',
        },
        {
          label: 'Summarize recent feed activity',
          text: 'Summarize recent activity in my feed and call out anything that needs attention.',
        },
        {
          label: 'What should I post next?',
          text: 'Based on recent workspace activity, what should I post or update next?',
        },
      ];
    case 'track_detail':
      return [
        {
          label: 'Help me add an entry here',
          text: ctx.focusedTrackId
            ? 'Help me draft a new entry for this track. What fields should I fill in?'
            : 'Help me draft a new entry for the track I am viewing.',
        },
        {
          label: 'Summarize this track',
          text: 'Summarize the entries in this track and highlight open items.',
        },
        {
          label: 'Suggest a view for this track',
          text: 'Suggest a useful view (board, table, or feed) for this track.',
        },
      ];
    case 'apps_list':
    case 'app_detail':
      return [
        {
          label: 'Ask Integral to scaffold an app',
          text: 'Help me scaffold or refine an app for this workspace — tracks, entry types, and views.',
        },
        {
          label: 'Explain this app structure',
          text: ctx.focusedAppId
            ? 'Explain the structure of the app I am viewing and how tracks relate.'
            : 'Explain how apps, tracks, and entries fit together in Integral.',
        },
      ];
    case 'settings':
      return [
        {
          label: 'Which harness should I use?',
          text: 'Explain the active harness options and when to use Integral vs Echo.',
        },
        {
          label: 'Help me get started',
          text: 'Walk me through getting started with Integral in this workspace.',
        },
      ];
    default:
      return [];
  }
}
