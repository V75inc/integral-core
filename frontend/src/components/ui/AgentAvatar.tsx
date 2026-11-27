import { BookOpen, Layers, MessageCircle, Search } from 'lucide-react';
import { LogoMark } from './Logo';
import { LINE_ICON_STROKE } from './IconWell';

const icons = { book: BookOpen, search: Search, message: MessageCircle, history: Layers };
const imagePrefix = /^data:image\/(png|jpeg|webp);base64,/;

export function AgentAvatar({ icon, avatar, size = 'sm' }: { icon?: string; avatar?: string | null; size?: 'sm' | 'portrait' }) {
  const Icon = icon && Object.prototype.hasOwnProperty.call(icons, icon)
    ? icons[icon as keyof typeof icons] : undefined;
  const portrait = size === 'portrait';
  const frame = portrait
    ? 'h-16 w-16 overflow-hidden rounded-2xl bg-[var(--ai-bg)] text-[var(--ai-fg)]'
    : 'h-8 w-8';
  return <span className={`inline-flex shrink-0 items-center justify-center ${frame}`} aria-hidden="true">
    {avatar && imagePrefix.test(avatar)
      ? <img src={avatar} alt="" className="h-full w-full rounded-[var(--radius-input)] object-cover" />
      : Icon ? <Icon size={portrait ? 36 : 20} strokeWidth={LINE_ICON_STROKE} /> : <LogoMark size={portrait ? 'lg' : 'sm'} />}
  </span>;
}
