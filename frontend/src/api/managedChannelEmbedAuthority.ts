/** Shared canonical HTTPS checks for manager projection and exact embed proof. */
export function canonicalManagedEmbedHttps(raw: unknown): URL | null {
  if (typeof raw !== 'string' || raw.length > 2048 || !raw.startsWith('https://') ||
      !/^[\x21-\x7e]+$/.test(raw) || /[\\?#]/.test(raw)) return null;
  try {
    const parsed = new URL(raw);
    const host = parsed.hostname;
    const terminalLabel = host.slice(host.lastIndexOf('.') + 1);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password ||
        parsed.search || parsed.hash || parsed.port === '0' || host === 'localhost' || host.endsWith('.localhost') ||
        host.startsWith('127.') || host === '0.0.0.0' ||
        /^[0-9]+$/.test(terminalLabel) || terminalLabel.startsWith('0x') ||
        !host.split('.').every(label => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** A parent origin is canonical, HTTPS and never an address with a path. */
export function isCanonicalManagedEmbedOrigin(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 261 && canonicalManagedEmbedHttps(value)?.origin === value;
}
