export function hasAssistantDebugPayload(custom: unknown): boolean {
  return Boolean(
    custom &&
      typeof custom === "object" &&
      (custom as { finalPayload?: unknown }).finalPayload &&
      typeof (custom as { finalPayload?: unknown }).finalPayload === "object",
  );
}
