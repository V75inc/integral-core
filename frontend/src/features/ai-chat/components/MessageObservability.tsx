import { useMessageTiming, useAuiState } from "@assistant-ui/react";
import type { ObservabilityStep } from "../useAIChatRuntime";

/** jvagent final-payload usage block (interaction.usage). */
type FinalUsage = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  total_duration_seconds?: number;
  estimated_cost_usd?: number;
};

/** jvagent final-payload per-call metric (interaction.observability_metrics[]). */
type FinalMetric = {
  event_type?: string;
  data?: {
    model?: string;
    duration?: number;
    usage?: { prompt_tokens?: number; completion_tokens?: number };
  };
};

function formatTimeSec(seconds: number | undefined): string | null {
  if (seconds == null || seconds <= 0) return null;
  return formatTime(seconds * 1000);
}

function formatExactTokens(n: number | undefined): string {
  return n == null ? "—" : n.toLocaleString();
}

function formatTime(ms: number | undefined): string | null {
  if (ms == null || ms <= 0) return null;
  return ms >= 1_000 ? `${(ms / 1_000).toFixed(1)}s` : `${Math.round(ms)}ms`;
}

function formatTps(tps: number | undefined): string | null {
  if (tps == null || tps <= 0) return null;
  return `${tps.toFixed(1)} tok/s`;
}

function shortModel(modelId: string | undefined): string {
  if (!modelId) return "unknown";
  const parts = modelId.split("/");
  return parts[parts.length - 1];
}

/** Model names share the activity disclosure, including turns without tools. */
export function useMessageModelLabel(): string | undefined {
  return useAuiState((s) => {
    const custom = s.message.metadata?.custom as {
      steps?: ObservabilityStep[];
      finalPayload?: { interaction?: { observability_metrics?: FinalMetric[] } };
    } | undefined;
    const models: string[] = (custom?.steps ?? []).flatMap(step => step.modelId ? [step.modelId] : []);
    if (models.length === 0) {
      models.push(...(custom?.finalPayload?.interaction?.observability_metrics ?? [])
        .filter(metric => metric.event_type === "model_call")
        .flatMap(metric => metric.data?.model ? [metric.data.model] : []));
    }
    const unique = [...new Set(models)];
    return unique.length ? unique.map(shortModel).join(" / ") : undefined;
  });
}

export function MessageObservability() {
  const role = useAuiState((s) => s.message.role);
  const statusType = useAuiState((s) =>
    s.message.role === "assistant" ? s.message.status.type : null,
  );
  const customSteps = useAuiState(
    (s) => (s.message.metadata?.custom as { steps?: ObservabilityStep[] })?.steps,
  );
  // Authoritative usage from jvagent's final payload (interaction.usage):
  // {prompt_tokens, completion_tokens, total_tokens, ...}. This is the source
  // of truth for token spend — observability_metrics-derived steps can be
  // empty when the metric shape doesn't match the per-step extractor.
  const finalUsage = useAuiState(
    (s) =>
      (
        (s.message.metadata?.custom as { finalPayload?: { interaction?: { usage?: FinalUsage } } })
          ?.finalPayload?.interaction?.usage
      ) ?? undefined,
  );
  // Authoritative per-model_call metrics from the final payload — the model
  // name lives here (`data.model`); the per-step extractor's `steps` is often
  // empty (metric-shape mismatch), so this is the reliable model source.
  const finalMetrics = useAuiState(
    (s) =>
      (
        s.message.metadata?.custom as {
          finalPayload?: {
            interaction?: { observability_metrics?: FinalMetric[] };
          };
        }
      )?.finalPayload?.interaction?.observability_metrics ?? undefined,
  );
  const timing = useMessageTiming();

  if (role !== "assistant") return null;
  if (statusType === "running") return null;

  const steps = customSteps ?? [];
  const durationFromFinal =
    finalUsage?.total_duration_seconds != null &&
    finalUsage.total_duration_seconds > 0
      ? finalUsage.total_duration_seconds * 1000
      : undefined;
  const totalStreamMs =
    (timing?.totalStreamTime ?? 0) > 0
      ? timing!.totalStreamTime
      : durationFromFinal;
  const hasTiming = (totalStreamMs ?? 0) > 0;

  // Prefer the final payload's authoritative total; fall back to summing the
  // per-step usage (older turns / non-jvagent providers).
  const allInputReported = steps.length > 0 && steps.every((st) => st.usage?.inputTokens != null);
  const allOutputReported = steps.length > 0 && steps.every((st) => st.usage?.outputTokens != null);
  const totalIn =
    finalUsage?.prompt_tokens ??
    (allInputReported
      ? steps.reduce((sum, st) => sum + (st.usage?.inputTokens ?? 0), 0)
      : undefined);
  const totalOut =
    finalUsage?.completion_tokens ??
    (allOutputReported
      ? steps.reduce((sum, st) => sum + (st.usage?.outputTokens ?? 0), 0)
      : undefined);
  const totalTokens =
    finalUsage?.total_tokens ??
    (totalIn != null && totalOut != null ? totalIn + totalOut : 0);

  // Per-model_call rows for the breakdown table — driven by the final
  // payload's observability_metrics (the per-step extractor's `steps` is often
  // empty), mirroring jvchat's stats readout. embedding_call rolls into totals
  // but isn't listed as a step.
  const metricSteps = (finalMetrics ?? []).filter(
    (m) => m.event_type === "model_call",
  );
  const metricModels = metricSteps
    .map((m) => m.data?.model)
    .filter((m): m is string => !!m);
  const hasModel = !!(steps[0]?.modelId ?? metricModels[0]);
  const providerCostComplete =
    steps.length > 0 && steps.every((step) => step.providerCostUsd != null);
  const providerCostTotal = providerCostComplete
    ? steps.reduce((sum, step) => sum + (step.providerCostUsd ?? 0), 0)
    : undefined;
  const hasNativeCallDetails = steps.some(
    (step) => step.durationMs != null || step.provider != null || step.outcome != null,
  );

  if (
    steps.length === 0 &&
    metricSteps.length === 0 &&
    !hasTiming &&
    totalTokens === 0 &&
    !hasModel
  ) {
    return null;
  }

  const timeStr = formatTime(totalStreamMs);
  const tpsStr = formatTps(timing?.tokensPerSecond);

  return (
    <div data-slot="aui_usage-details" className="rounded-[var(--radius-input)] bg-[var(--panel-2)] px-3 py-2 text-[11px] text-[var(--text-muted)]">
      {hasNativeCallDetails && (
        <div className="overflow-x-auto">
        <table className="w-full text-left tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">
              <th className="pb-1 pr-3 font-medium">Call</th>
              <th className="pb-1 pr-3 font-medium">Model</th>
              <th className="pb-1 pr-3 font-medium text-right">In</th>
              <th className="pb-1 pr-3 font-medium text-right">Out</th>
              <th className="pb-1 pr-3 font-medium text-right">Cost</th>
              <th className="pb-1 pr-3 font-medium text-right">Time</th>
              <th className="pb-1 font-medium text-right">State</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step, index) => (
              <tr key={step.requestId ?? index}>
                <td className="pr-3 py-0.5">{index + 1}</td>
                <td className="pr-3 py-0.5 font-mono text-[10px]">
                  <span title={step.modelId ?? step.provider}>
                    {step.modelId ?? step.provider ?? "unknown"}
                  </span>
                </td>
                <td className="pr-3 py-0.5 text-right">
                  {formatExactTokens(step.usage?.inputTokens)}
                </td>
                <td className="pr-3 py-0.5 text-right">
                  {formatExactTokens(step.usage?.outputTokens)}
                </td>
                <td
                  className="pr-3 py-0.5 text-right"
                  title={step.costSource ? `Source: ${step.costSource}` : undefined}
                >
                  {step.providerCostUsd == null
                    ? "—"
                    : `$${step.providerCostUsd.toFixed(4)}`}
                </td>
                <td className="pr-3 py-0.5 text-right">
                  {formatTime(step.durationMs) ?? "—"}
                </td>
                <td className="py-0.5 text-right">{step.outcome ?? "—"}</td>
              </tr>
            ))}
            {steps.length > 1 && (
              <tr className="border-t border-[var(--border-subtle)] text-[var(--text)]">
                <td className="pr-3 pt-1" colSpan={2}>Total</td>
                <td className="pr-3 pt-1 text-right">{formatExactTokens(totalIn)}</td>
                <td className="pr-3 pt-1 text-right">{formatExactTokens(totalOut)}</td>
                <td className="pr-3 pt-1 text-right">
                  {providerCostTotal == null
                    ? "—"
                    : `$${providerCostTotal.toFixed(4)}`}
                </td>
                <td className="pt-1" colSpan={2} />
              </tr>
            )}
          </tbody>
        </table>
        </div>
      )}
      {metricSteps.length > 0 && (
        <table className="w-full text-left tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-[var(--text-subtle)]">
              <th className="pb-1 pr-3 font-medium">Step</th>
              <th className="pb-1 pr-3 font-medium">Model</th>
              <th className="pb-1 pr-3 font-medium text-right">In</th>
              <th className="pb-1 pr-3 font-medium text-right">Out</th>
              <th className="pb-1 font-medium text-right">Time</th>
            </tr>
          </thead>
          <tbody>
            {metricSteps.map((step, i) => (
              <tr key={i}>
                <td className="pr-3 py-0.5">{i + 1}</td>
                <td className="pr-3 py-0.5 font-mono text-[10px]">
                  {shortModel(step.data?.model)}
                </td>
                <td className="pr-3 py-0.5 text-right">
                  {formatExactTokens(step.data?.usage?.prompt_tokens)}
                </td>
                <td className="pr-3 py-0.5 text-right">
                  {formatExactTokens(step.data?.usage?.completion_tokens)}
                </td>
                <td className="py-0.5 text-right">
                  {formatTimeSec(step.data?.duration) ?? "—"}
                </td>
              </tr>
            ))}
            {metricSteps.length > 1 && (
              <tr className="border-t border-[var(--border-subtle)] text-[var(--text)]">
                <td className="pr-3 pt-1" colSpan={2}>
                  Total
                </td>
                <td className="pr-3 pt-1 text-right">
                  {formatExactTokens(totalIn)}
                </td>
                <td className="pr-3 pt-1 text-right">
                  {formatExactTokens(totalOut)}
                </td>
                <td className="pt-1" />
              </tr>
            )}
          </tbody>
        </table>
      )}

      {(hasTiming || totalTokens > 0 || steps.length > 0) && (
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-[var(--text-subtle)]">
          {timing?.firstTokenTime != null && timing.firstTokenTime > 0 && (
            <span>TTFT: {formatTime(timing.firstTokenTime)}</span>
          )}
          {timeStr && <span>Total: {timeStr}</span>}
          {tpsStr && <span>{tpsStr}</span>}
          {totalTokens > 0 && (
            <span>{totalTokens.toLocaleString()} tokens</span>
          )}
          {providerCostTotal != null && (
            <span>{steps.some(step => step.costSource === "litellm_calculated") ? "Estimated cost" : "Provider cost"} ${providerCostTotal.toFixed(4)}</span>
          )}
          {hasNativeCallDetails && providerCostTotal == null && (
            <span>Provider cost unavailable for some calls</span>
          )}
          {typeof finalUsage?.estimated_cost_usd === "number" &&
            finalUsage.estimated_cost_usd > 0 && (
              <span>${finalUsage.estimated_cost_usd.toFixed(4)}</span>
            )}
        </div>
      )}
    </div>
  );
}
