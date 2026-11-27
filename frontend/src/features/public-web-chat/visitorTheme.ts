import { useEffect, useState } from 'react';

export type VisitorTheme = 'light' | 'dark' | 'system';
export function parseVisitorTheme(value: string | null): VisitorTheme {
  return value === 'light' || value === 'dark' ? value : 'system';
}

export function useVisitorDarkTheme(): boolean {
  const theme = parseVisitorTheme(document.querySelector('[data-visitor-theme]')?.getAttribute('data-visitor-theme') ?? null);
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false);
  useEffect(() => {
    if (theme !== 'system' || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const sync = () => setSystemDark(query.matches);
    sync();
    query.addEventListener('change', sync);
    return () => query.removeEventListener('change', sync);
  }, [theme]);
  return theme === 'dark' || (theme === 'system' && systemDark);
}
