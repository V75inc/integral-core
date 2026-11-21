import { BookOpen, Layers, MessageCircle, Search } from 'lucide-react';
import { LogoMark } from './Logo';
import { LINE_ICON_STROKE } from './IconWell';

const icons = { book: BookOpen, search: Search, message: MessageCircle, history: Layers };
const imagePrefix = /^data:image\/(png|jpeg|webp);base64,/;

export function AgentAvatar({ icon, avatar }: { icon?: string; avatar?: string | null }) {
  const Icon = icon && Object.prototype.hasOwnProperty.call(icons, icon)
    ? icons[icon as keyof typeof icons] : undefined;
  return <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center" aria-hidden="true">
    {avatar && imagePrefix.test(avatar)
      ? <img src={avatar} alt="" className="h-full w-full rounded-[var(--radius-input)] object-cover" />
      : Icon ? <Icon size={20} strokeWidth={LINE_ICON_STROKE} /> : <LogoMark size="sm" />}
  </span>;
}
