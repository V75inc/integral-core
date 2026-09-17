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
    };

export function isExtensionMessage(data: unknown): data is ExtensionBridgeMessage {
  if (!data || typeof data !== 'object') return false;
  const msg = data as ExtensionBridgeMessage;
  return msg.protocol === EXTENSION_PROTOCOL && typeof msg.type === 'string';
}
