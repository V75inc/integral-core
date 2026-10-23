export { defineHome, defineComponent, NATIVE_HOME_COMPONENTS } from './native.js';
export type { NativeHome, HomeElement, HomeInput } from './native.js';
/** Public client for the existing Integral extension protocol. No Core imports. */
export const PROTOCOL = 'integral.extension.v1';
export type NavigationTarget = { kind: 'app' | 'track' | 'entry' | 'dashboard'; id: string };
export type Theme = Record<string, string>;
export interface HostContext<P = Record<string, unknown>> {
  appId: string;
  workspaceId: string;
  viewKey: string;
  mountId: string;
  packageVersion?: string;
  props: P;
  theme: Theme;
  data: Readonly<Record<string, unknown>>;
}
export interface QueryResponse<T> {
  app_id: string;
  query_key: string;
  output: T;
  object_refs?: Array<Record<string, unknown>>;
  evidence?: Record<string, unknown>;
}
export interface OperationReceipt { id: string; status: 'succeeded'; replayed: boolean }
export type OperationResponse<T> = {
  output_disposition?: 'available'; output: T; operation_receipt?: OperationReceipt | null;
  object_refs?: Array<Record<string, unknown>>; evidence?: Record<string, unknown>;
} | { output_disposition: 'withheld'; current_state_verified: false; operation_receipt: OperationReceipt };
export class IntegralUIError extends Error {
  constructor(message: string, readonly code: string, readonly retryable = false,
    readonly value?: unknown, readonly outcome?: 'unconfirmed') {
    super(message); this.name = 'IntegralUIError';
  }
}
export interface RequestOptions { signal?: AbortSignal; timeoutMs?: number }
export interface InvokeOptions extends RequestOptions { idempotencyKey: string }
export interface ClientOptions {
  /** Defaults to window; injectable for independent contract tests. */
  window?: Window;
  timeoutMs?: number;
}
interface Pending {
  resolve(value: unknown): void;
  reject(error: unknown): void;
  cleanup(): void;
  type: string;
}

/** One client per mounted view. Never retries operations or generates intent keys. */
export class IntegralUIClient<P = Record<string, unknown>> {
  readonly ready: Promise<Readonly<HostContext<P>>>;
  private win: Window;
  private token = '';
  private origin = '*';
  private disposed = false;
  private state?: HostContext<P>;
  private pending = new Map<string, Pending>();
  private subscribers = new Set<(context: Readonly<HostContext<P>>) => void>();
  private events = new Set<(event: { type: string; state?: string }) => void>();
  private resolveReady!: (context: Readonly<HostContext<P>>) => void;
  private rejectReady!: (error: unknown) => void;
  private readyTimer: ReturnType<typeof setTimeout>;
  private sequence = 0;
  private timeout: number;

  constructor(options: ClientOptions = {}) {
    this.win = options.window ?? window;
    this.timeout = options.timeoutMs ?? 15000;
    this.ready = new Promise((resolve, reject) => { this.resolveReady = resolve; this.rejectReady = reject; });
    // Observing readiness is optional; a missing host must not cause an unhandled rejection.
    void this.ready.catch(() => {});
    this.readyTimer = setTimeout(() => this.rejectReady(new IntegralUIError('Integral host did not respond.', 'host_unavailable')), this.timeout);
    this.win.addEventListener('message', this.receive);
    this.post({ type: 'ready' });
  }
  get context(): Readonly<HostContext<P>> | undefined { return this.state; }
  subscribe(callback: (context: Readonly<HostContext<P>>) => void): () => void {
    this.subscribers.add(callback);
    if (this.state) callback(this.state);
    return () => { this.subscribers.delete(callback); };
  }
  onEvent(callback: (event: { type: string; state?: string }) => void): () => void {
    this.events.add(callback); return () => { this.events.delete(callback); };
  }
  private post(message: Record<string, unknown>) {
    if (!this.disposed) this.win.parent.postMessage({ protocol: PROTOCOL, ...message }, this.origin);
  }
  private receive = (event: MessageEvent) => {
    if (this.disposed || event.source !== this.win.parent || !event.data || event.data.protocol !== PROTOCOL) return;
    if (this.token && event.origin !== this.origin) return;
    const message = event.data;
    if (message.type === 'handshake') {
      if (typeof message.token !== 'string' || !message.token ||
          !['appId', 'workspaceId', 'viewKey'].every(key => typeof message[key] === 'string' && message[key])) return;
      if (this.token && (this.token !== message.token || this.state?.appId !== message.appId || this.state?.workspaceId !== message.workspaceId || this.state?.viewKey !== message.viewKey)) this.cancelPending('View scope changed.', 'scope_changed');
      this.token = message.token;
      this.origin = event.origin;
      const data = message.context && typeof message.context === 'object' ? message.context : this.state?.data ?? {};
      this.state = Object.freeze({ appId: message.appId, workspaceId: message.workspaceId, viewKey: message.viewKey,
        mountId: String(data.mountId ?? message.viewKey), packageVersion: message.packageVersion,
        theme: Object.freeze({ ...message.theme }), props: data.props ?? {}, data: Object.freeze({ ...data }) });
      clearTimeout(this.readyTimer); this.resolveReady(this.state);
      this.subscribers.forEach(callback => callback(this.state!));
      return;
    }
    if (!this.token) return;
    if (typeof message.requestId === 'string' && typeof message.type === 'string' && message.type.endsWith('.result')) {
      const pending = this.pending.get(message.requestId);
      if (!pending || message.type !== `${pending.type}.result`) return;
      this.pending.delete(message.requestId); pending.cleanup();
      if (message.ok === true && message.value?.output_disposition !== 'withheld') pending.resolve(message.value);
      else pending.reject(new IntegralUIError(message.error || 'Integral request failed.',
        message.value?.output_disposition === 'withheld' ? 'operation_output_withheld' : message.error_code || 'request_failed',
        message.retryable === true, message.value, message.outcome === 'unconfirmed' ? 'unconfirmed' : undefined));
      return;
    }
    if (message.type === 'refresh') {
      // Legacy hosts do not include context in updates; reread through the bridge.
      void this.read<Record<string, unknown>>('context').then(data => {
        if (!this.state || this.disposed) return;
        this.state = Object.freeze({ ...this.state, data: Object.freeze({ ...data }), props: (data.props ?? {}) as P });
        this.subscribers.forEach(callback => callback(this.state!));
      }).catch(() => {});
    }
    if (['refresh', 'lifecycle', 'draft.committed'].includes(message.type)) this.events.forEach(callback => callback(message));
  };
  private async request<T>(type: string, payload: Record<string, unknown>, options: RequestOptions = {}): Promise<T> {
    if (this.disposed) throw new IntegralUIError('UI client is closed.', 'disposed');
    if (options.signal?.aborted) throw new IntegralUIError('Request cancelled before dispatch.', 'aborted');
    if (options.signal) {
      const signal = options.signal;
      await new Promise<void>((resolve, reject) => {
        const abort = () => { cleanup(); reject(new IntegralUIError('Request cancelled before dispatch.', 'aborted')); };
        const cleanup = () => signal.removeEventListener('abort', abort);
        signal.addEventListener('abort', abort, { once: true });
        if (signal.aborted) { abort(); return; }
        this.ready.then(() => { cleanup(); resolve(); }, error => { cleanup(); reject(error); });
      });
    } else await this.ready;
    if (this.disposed) throw new IntegralUIError('UI client is closed.', 'disposed');
    if (options.signal?.aborted) throw new IntegralUIError('Request cancelled before dispatch.', 'aborted');
    if (type === 'operation' && this.state?.data.preview) throw new IntegralUIError('Business actions are disabled in previews.', 'preview_read_only');
    const requestId = `${Date.now()}-${++this.sequence}`;
    return new Promise<T>((resolve, reject) => {
      const unconfirmed = type === 'operation';
      const failure = (message: string, code: string) => new IntegralUIError(message, code, false, undefined, unconfirmed ? 'unconfirmed' : undefined);
      const abort = () => { this.pending.delete(requestId); cleanup(); reject(failure('Request cancelled. An admitted operation may still complete.', 'aborted')); };
      const timer = setTimeout(() => {
        this.pending.delete(requestId); cleanup();
        reject(failure('Result not received. Check current state before retrying the same intent.', 'timeout'));
      }, options.timeoutMs ?? this.timeout);
      const cleanup = () => { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); };
      this.pending.set(requestId, { resolve: value => resolve(value as T), reject, cleanup, type });
      options.signal?.addEventListener('abort', abort, { once: true });
      this.post({ type, requestId, ...payload });
    });
  }
  read<T = unknown>(path: string, options?: RequestOptions): Promise<T> { return this.request('read', { path }, options); }
  query<T = Record<string, unknown>>(capabilityKey: string, params: Record<string, unknown> = {}, options?: RequestOptions): Promise<QueryResponse<T>> {
    return this.request('query', { capabilityKey, params }, options);
  }
  invoke<T = Record<string, unknown>>(operationKey: string, payload: Record<string, unknown>, options: InvokeOptions): Promise<OperationResponse<T>> {
    if (typeof options?.idempotencyKey !== 'string' || !options.idempotencyKey.trim()) return Promise.reject(new IntegralUIError('An operation requires a stable intent key.', 'idempotency_key_required'));
    if (this.state?.data.preview) return Promise.reject(new IntegralUIError('Business actions are disabled in previews.', 'preview_read_only'));
    return this.request('operation', { operationKey, payload, idempotencyKey: options.idempotencyKey }, options);
  }
  capabilities<T = unknown>(options?: RequestOptions): Promise<T> { return this.request('capabilities', {}, options); }
  navigate(target: NavigationTarget, options?: RequestOptions): Promise<{ path: string }> {
    return this.request('navigation', { target }, options);
  }
  resize(height: number | 'fill'): void { this.post({ type: 'resize', height }); }
  observeContentSize(): () => void {
    const observer = new ResizeObserver(() => this.resize(Math.ceil(this.win.document.body.getBoundingClientRect().height)));
    observer.observe(this.win.document.body); return () => observer.disconnect();
  }
  private cancelPending(message: string, code: string) {
    for (const pending of this.pending.values()) {
      pending.cleanup(); pending.reject(new IntegralUIError(message, code, false, undefined, pending.type === 'operation' ? 'unconfirmed' : undefined));
    }
    this.pending.clear();
  }
  dispose(): void {
    this.disposed = true; clearTimeout(this.readyTimer);
    this.win.removeEventListener('message', this.receive);
    this.rejectReady(new IntegralUIError('UI client is closed.', 'disposed'));
    this.cancelPending('UI client is closed. An admitted operation may still complete.', 'disposed');
    this.subscribers.clear(); this.events.clear();
  }
}
export function createClient<P = Record<string, unknown>>(options?: ClientOptions): IntegralUIClient<P> { return new IntegralUIClient<P>(options); }
