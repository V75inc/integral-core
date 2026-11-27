import { useId } from 'react';
import { Check, Monitor, Moon, Sun } from 'lucide-react';

import { Text } from '../../../ui';
import type { VisitorTheme } from '../../public-web-chat/visitorTheme';
import './webChatAppearance.css';

const themes = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
] as const;

export function WebChatAppearance({ value, disabled, onChange }: {
  value: VisitorTheme;
  disabled: boolean;
  onChange: (value: VisitorTheme) => void;
}) {
  const name = useId();
  return <fieldset aria-label="Visitor chat theme" disabled={disabled} className="web-chat-appearance">
    {themes.map(({ value: theme, label, Icon }) => (
      <label key={theme} className="web-chat-appearance-option">
        <input className="sr-only" name={name} type="radio" checked={theme === value}
          onChange={() => onChange(theme)} />
        <Icon size={15} aria-hidden="true" />
        <Text as="span" variant="body-sm" weight="medium">{label}</Text>
        {theme === value && <Check size={13} aria-hidden="true" className="web-chat-appearance-check" />}
      </label>
    ))}
  </fieldset>;
}
