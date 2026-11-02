/** Map native home card node ids → home action label (Recruitment OM). */
export const RECRUITMENT_NATIVE_CARD_ACTION_LABELS: Record<string, string> = {
  step_openings_card: 'Post a job opening',
  step_pipeline_card: 'Create a candidate',
};

/** Map native home card node ids → home action label (Human Resources OM). */
export const HUMAN_RESOURCES_NATIVE_CARD_ACTION_LABELS: Record<string, string> = {
  step_roster_card: 'Add an employee',
  step_onboarding_card: 'Start onboarding',
  step_requests_card: 'Review time-off',
};

const NATIVE_CARD_ACTION_LABELS_BY_PACKAGE_SLUG: Record<
  string,
  Record<string, string>
> = {
  recruitment: RECRUITMENT_NATIVE_CARD_ACTION_LABELS,
  'human-resources': HUMAN_RESOURCES_NATIVE_CARD_ACTION_LABELS,
};

export function nativeCardActionLabelsForPackage(
  packageSlug: string,
): Record<string, string> | undefined {
  const key = packageSlug.trim().toLowerCase();
  return key ? NATIVE_CARD_ACTION_LABELS_BY_PACKAGE_SLUG[key] : undefined;
}

export type CardChatAction = { label: string; draft: string };

export function buildNativeCardChatActions(
  homeActions: Array<{ label: string; draft: string }>,
  cardIdToActionLabel: Record<string, string>,
): Record<string, CardChatAction> {
  const byLabel = new Map(homeActions.map(action => [action.label, action] as const));
  const out: Record<string, CardChatAction> = {};
  for (const [cardId, label] of Object.entries(cardIdToActionLabel)) {
    const action = byLabel.get(label);
    if (action) {
      out[cardId] = { label: action.label, draft: action.draft };
    }
  }
  return out;
}
