/** Map public install denial codes to Business guidance; no text parsing or plan assumptions. */
export function resolveBusinessInstallDenial(err: unknown, onAction: () => void) {
  const record = (value: unknown): Record<string, unknown> | null =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const root = record(err);
  const response = record(root?.response);
  const data = record(response?.data) ?? root;
  const envelope = record(data?.detail) ?? data;
  if (envelope?.error_code !== 'entitlement.required') return null;
  return {
    message: 'App access is required. Review this workspace’s app access before installing.',
    actionLabel: 'Review app access',
    onAction,
  };
}
