import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  aiConfigurationApi,
  AI_CONFIGURATION_QUERY_KEY,
  type AIConfiguration,
  type ChatLimitKey,
} from "../../../api/aiConfiguration";
import { errorMessageFromAxios } from "../../../api/helpers";
import { Button } from "../../../components/ui/Button";
import { useToast } from "../../../context/ToastContext";
import { Input, Text } from "../../../ui";

const fields: { key: ChatLimitKey; label: string; min: number }[] = [
  { key: "request_limit", label: "Model requests per turn", min: 1 },
  { key: "tool_call_limit", label: "Tool calls per turn", min: 1 },
  { key: "token_limit", label: "Total tokens per turn", min: 1 },
  { key: "run_seconds", label: "Turn timeout (seconds)", min: 30 },
  { key: "request_timeout", label: "Request timeout (seconds)", min: 10 },
  { key: "idle_timeout", label: "Idle timeout (seconds)", min: 5 },
];

export function DeploymentLimits({ data }: { data: AIConfiguration }) {
  const settings = data.deployment_limit_settings;
  const [draft, setDraft] = useState<{
    revision: number;
    protection: boolean;
    values: Partial<Record<ChatLimitKey, string>>;
  } | null>(null);
  const cache = useQueryClient();
  const { showToast } = useToast();
  const save = useMutation({
    mutationFn: aiConfigurationApi.saveDeploymentLimits,
    onSuccess: async (saved) => {
      cache.setQueriesData<AIConfiguration>(
        { queryKey: AI_CONFIGURATION_QUERY_KEY },
        (current) => {
          if (
            !current ||
            (current.deployment_limit_settings?.revision ?? 0) > saved.revision
          )
            return current;
          return {
            ...current,
            harness_limits: saved.effective,
            ...(current.deployment_limit_settings && {
              deployment_limit_settings: saved,
            }),
          };
        },
      );
      setDraft(null);
      await cache.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY });
      showToast("Deployment limits saved.", "success");
    },
    onError: () => {
      void cache.invalidateQueries({ queryKey: AI_CONFIGURATION_QUERY_KEY });
    },
  });
  function edit() {
    if (!settings) return;
    setDraft({
      revision: settings.revision,
      protection: settings.whole_answer_protection ?? false,
      values: Object.fromEntries(
        Object.entries(settings.overrides).map(([k, v]) => [k, String(v)]),
      ),
    });
    save.reset();
  }
  if (draft && settings) {
    const stale = draft.revision !== settings.revision;
    const overrides = Object.fromEntries(
      Object.entries(draft.values)
        .filter(([, v]) => v !== "")
        .map(([k, v]) => [k, Number(v)]),
    );
    const valid = fields.every(
      ({ key, min }) =>
        overrides[key] === undefined ||
        (Number.isSafeInteger(overrides[key]) &&
          overrides[key] >= min &&
          overrides[key] <= settings.host[key]),
    );
    const protectionChanged = draft.protection !== (settings.whole_answer_protection ?? false);
    const dirty = protectionChanged || fields.some(
      ({ key }) => overrides[key] !== settings.overrides[key],
    );
    function submit(event: FormEvent) {
      event.preventDefault();
      if (draft && valid && dirty && !stale && !save.isPending)
        save.mutate({ revision: draft.revision, overrides, ...(protectionChanged && { whole_answer_protection: draft.protection }) });
    }
    return (
      <form className="space-y-4" onSubmit={submit}>
        <Text as="p" variant="body-sm" tone="muted">
          Applies to new native chat turns in every workspace. Leave a field
          blank to follow its host maximum.
        </Text>
        <fieldset
          disabled={save.isPending}
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        >
          {fields.map(({ key, label, min }) => (
            <label key={key} className="min-w-0 space-y-1">
              <Text as="span" variant="label">
                {label}
              </Text>
              <Input
                type="number"
                step={1}
                min={min}
                max={settings.host[key]}
                aria-label={label}
                placeholder={String(settings.host[key])}
                value={draft.values[key] ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    values: { ...draft.values, [key]: e.target.value },
                  })
                }
              />
              <Text as="span" variant="meta" tone="muted">
                Host maximum {settings.host[key].toLocaleString()}
              </Text>
            </label>
          ))}
        </fieldset>
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={draft.protection} disabled={save.isPending}
            onChange={(event) => setDraft({ ...draft, protection: event.target.checked })} />
          <span><Text as="span" variant="label">Whole-answer protection</Text>
            <Text as="span" className="block" variant="meta" tone="muted">Wait for final checks before showing ordinary replies. When off, early text can change. Workspace operations and private profiles always wait for final checks.</Text>
          </span>
        </label>
        {stale ? (
          <div role="alert">
            <Text variant="body-sm">
              Another administrator changed these limits.
            </Text>{" "}
            <Button type="button" size="sm" variant="ghost" onClick={edit}>
              Load current values
            </Button>
          </div>
        ) : (
          save.isError && (
            <Text as="p" role="alert" variant="body-sm" tone="danger">
              {errorMessageFromAxios(
                save.error,
                "Could not save deployment limits.",
              )}
            </Text>
          )
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={save.isPending || !Object.keys(overrides).length}
            onClick={() => setDraft({ ...draft, values: {} })}
          >
            Use host defaults
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={save.isPending}
            onClick={() => setDraft(null)}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            disabled={stale || !valid || !dirty || save.isPending}
            loading={save.isPending}
          >
            Save limits
          </Button>
        </div>
      </form>
    );
  }
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {fields.map(({ key, label }) => (
          <div key={key}>
            <Text as="dt" variant="label" tone="muted">
              {label}
            </Text>
            <Text as="dd" variant="body" numeric>
              {(data.harness_limits[key] ?? 32).toLocaleString()}
            </Text>
          </div>
        ))}
        {settings && <div><Text as="dt" variant="label" tone="muted">Whole-answer protection</Text><Text as="dd" variant="body">{settings.whole_answer_protection ? "On" : "Off"}</Text></div>}
      </dl>
      <div className="flex flex-wrap items-center justify-end gap-3">
        <Text as="p" variant="meta" tone="muted" className="mr-auto">
          Model profiles can use lower limits. Approval and access checks always
          apply.
          {!settings &&
            " Only a platform administrator can change these limits."}
        </Text>
        {settings && (
          <Button type="button" size="sm" variant="secondary" onClick={edit}>
            Edit deployment limits
          </Button>
        )}
      </div>
    </div>
  );
}
