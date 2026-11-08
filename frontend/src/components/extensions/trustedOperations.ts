import { AppUIError } from '@integral/sdk-react';
export { AppUIError } from '@integral/sdk-react';
import { isAxiosError } from 'axios';
import { RecordedOperationOutputWithheld, requireOperationOutput, type AppOperationInvokeResponse } from '../../api/extensions';

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}
const inflight = new Map<string, Promise<ReturnType<typeof requireOperationOutput>>>();
/** One durable key for an exact user intent; an uncertain write is never retried here. */
export async function invokeTrustedOperation(namespace: string[], key: string, input: Record<string, unknown>,
  dispatch: (input: Record<string, unknown>, intent: string) => Promise<AppOperationInvokeResponse>,
  current: () => boolean) {
  const payload = JSON.parse(JSON.stringify(input)) as Record<string, unknown>;
  const bytes = new TextEncoder().encode(JSON.stringify([namespace, key, canonical(payload)]));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const storageKey = `integral.ui.intent.${Array.from(new Uint8Array(digest)).map(byte => byte.toString(16).padStart(2, '0')).join('')}`;
  if (!current()) throw new AppUIError('This view is no longer active.');
  const existing = inflight.get(storageKey);
  if (existing) return existing;
  const intent = sessionStorage.getItem(storageKey) || crypto.randomUUID();
  // Refuse dispatch if durable retry identity cannot be retained.
  sessionStorage.setItem(storageKey, intent);
  const task = (async () => {
    try {
      const result = await dispatch(payload, intent);
      if (!current()) throw new AppUIError('Result unconfirmed because the view changed. Check the original record.', 'unconfirmed');
      sessionStorage.removeItem(storageKey);
      return requireOperationOutput(result);
    } catch (error) {
      const uncertain = error instanceof AppUIError ? error.outcome === 'unconfirmed' :
        isAxiosError(error) && (!error.response || error.response.status >= 500 || error.response.status === 408);
      if (uncertain) throw new AppUIError('Result unconfirmed. Check current records before retrying the same action.', 'unconfirmed');
      sessionStorage.removeItem(storageKey);
      if (error instanceof RecordedOperationOutputWithheld) throw new AppUIError(error.message, 'withheld', error.operationReceipt);
      if (isAxiosError(error)) {
        const message = error.response?.data?.message;
        throw new AppUIError(typeof message === 'string' ? message.slice(0, 2000) : 'This action was not accepted.');
      }
      throw error;
    } finally { inflight.delete(storageKey); }
  })();
  inflight.set(storageKey, task);
  return task;
}
