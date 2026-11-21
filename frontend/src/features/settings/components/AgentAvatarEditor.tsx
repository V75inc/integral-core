import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import * as Menu from '@radix-ui/react-dropdown-menu';
import { AgentAvatar } from '../../../components/ui/AgentAvatar';
import { Button } from '../../../components/ui/Button';
import { Surface, Text } from '../../../ui';
import type { ProfileAppearance, ProfileIcon } from '../../../api/connectionProfiles';

const PRESETS = [
  ['integral', 'Integral'],
  ['book', 'Book'],
  ['search', 'Search'],
  ['message', 'Message'],
] as const satisfies ReadonlyArray<readonly [ProfileIcon, string]>;
const TYPES = ['image/png', 'image/jpeg', 'image/webp'];

export function AgentAvatarEditor({ appearance, disabled, onChange, onReading }: {
  appearance: { icon?: string; avatar?: string | null };
  disabled: boolean;
  onChange: (value: ProfileAppearance) => void;
  onReading: (reading: boolean) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const reader = useRef<FileReader | null>(null);
  const [error, setError] = useState('');
  const [reading, setReading] = useState(false);
  useEffect(() => () => reader.current?.abort(), []);

  function choosePreset(icon: ProfileIcon) {
    setError('');
    onChange({ icon, avatar: null });
  }

  function onFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    setError('');
    if (!TYPES.includes(file.type) || !file.size || file.size > 512 * 1024) {
      setError('Choose a PNG, JPEG or WebP image up to 512 KB.');
      return;
    }
    const next = new FileReader();
    reader.current = next;
    setReading(true);
    onReading(true);
    next.onload = () => {
      if (typeof next.result === 'string') onChange({ avatar: next.result });
    };
    next.onerror = () => setError('Could not read that image. Try another file.');
    next.onloadend = () => {
      reader.current = null;
      setReading(false);
      onReading(false);
    };
    next.readAsDataURL(file);
  }

  const selected = appearance.avatar ? '' : appearance.icon ?? 'integral';
  const locked = disabled || reading;
  const menuItem = 'flex min-h-10 cursor-pointer items-center gap-2 rounded-[var(--radius-input)] px-2 text-sm outline-none interactive-panel-menu-item data-[highlighted]:ring-1 data-[highlighted]:ring-inset data-[highlighted]:ring-[var(--focus-ring-color)] data-[disabled]:opacity-50';
  return <section className="flex flex-col items-start gap-1.5" aria-label="Appearance">
    <Text as="span" variant="body-sm" weight="medium">Avatar</Text>
    <Menu.Root>
      <Menu.Trigger asChild>
        <Button type="button" size="xs" variant="secondary" aria-label="Choose avatar" disabled={locked}>
          <span role="img" aria-label="Avatar preview"><AgentAvatar {...appearance} /></span>
          <ChevronDown size={14} aria-hidden="true" />
        </Button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content asChild align="start" sideOffset={4} collisionPadding={12}>
          <Surface radius="input" elevation="pop"
          className="z-popover min-w-44 p-1">
          <Menu.RadioGroup className="flex gap-1" value={selected} aria-label="Avatar presets" onValueChange={value => choosePreset(value as ProfileIcon)}>
            {PRESETS.map(([icon, name]) => <Menu.RadioItem key={icon} value={icon} aria-label={name} title={name}
              className={`${menuItem} relative h-11 w-11 justify-center !px-0 interactive-panel-selected`}>
              <AgentAvatar icon={icon} />
              <Menu.ItemIndicator className="absolute right-0.5 top-0.5 text-[var(--brand-accent)]"><Check size={12} aria-hidden="true" /></Menu.ItemIndicator>
            </Menu.RadioItem>)}
          </Menu.RadioGroup>
          <Menu.Separator className="my-1 border-t border-[var(--border-subtle)]" />
          <Menu.Item className={menuItem} onSelect={() => input.current?.click()}>
            {appearance.avatar ? 'Change image' : 'Upload image'}
          </Menu.Item>
          <Menu.Item className={menuItem} disabled={selected === 'integral'} onSelect={() => choosePreset('integral')}>Reset to Integral</Menu.Item>
          <Text as="p" variant="meta" tone="muted" className="max-w-48 px-2 pb-1">PNG, JPEG or WebP up to 512 KB and 4 megapixels.</Text>
        </Surface>
          </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
    <input ref={input} type="file" accept={TYPES.join(',')} hidden onChange={onFile} />
    {reading && <Text as="p" role="status" variant="meta">Reading image…</Text>}
    {error && <Text as="p" role="alert" tone="danger">{error}</Text>}
  </section>;
}
