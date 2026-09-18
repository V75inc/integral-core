export const EXTENSION_PROTOCOL = 'integral.extension.v1';

export type ExtensionBridgeMessage =
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'handshake';
      token: string;
      workspaceId: string;
      appId: string;
      viewKey: string;
      packageVersion?: string;
      theme?: Record<string, unknown>;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'ready';
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'read';
      requestId: string;
      path: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'read.result';
      requestId: string;
      ok: boolean;
      value?: unknown;
      error?: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'operation';
      requestId: string;
      operationKey: string;
      payload?: Record<string, unknown>;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'operation.result';
      requestId: string;
      ok: boolean;
      value?: unknown;
      error?: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'capabilities';
      requestId: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'capabilities.result';
      requestId: string;
      ok: boolean;
      value?: unknown;
      error?: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'query';
      requestId: string;
      capabilityKey: string;
      params?: Record<string, unknown>;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'query.result';
      requestId: string;
      ok: boolean;
      value?: unknown;
      error?: string;
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'refresh';
    }
  | {
      protocol: typeof EXTENSION_PROTOCOL;
      type: 'lifecycle';
      state: 'paused' | 'unavailable' | 'active';
    };

export function isExtensionMessage(data: unknown): data is ExtensionBridgeMessage {
  if (!data || typeof data !== 'object') return false;
  const msg = data as ExtensionBridgeMessage;
  return msg.protocol === EXTENSION_PROTOCOL && typeof msg.type === 'string';
}
