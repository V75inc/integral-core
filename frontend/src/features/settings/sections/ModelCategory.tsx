import { ChevronDown, type LucideIcon } from "lucide-react";
import { Surface, Text } from "../../../ui";
import { IconWell, LINE_ICON_STROKE } from "../../../components/ui/IconWell";
import type { ReactNode } from "react";
import { SETTINGS_PANEL_BORDER } from "../components/settingsPanelBorder";

/** One predictable disclosure row for each type of AI model. */
export function ModelCategory({
  id,
  title,
  description,
  icon: Icon,
  open,
  onToggle,
  children,
  heading: Heading = "h3",
  prominent = false,
}: {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  heading?: "h3" | "h4" | "h5";
  prominent?: boolean;
}) {
  return (
    <Surface
      as="section"
      tone={prominent ? "panel" : "transparent"}
      border="none"
      radius="card"
      className={`overflow-hidden ${SETTINGS_PANEL_BORDER}`}
    >
      <Heading aria-label={title}>
        <button
          type="button"
          aria-label={title}
          aria-describedby={`${id}-description`}
          id={`${id}-heading`}
          aria-expanded={open}
          aria-controls={`${id}-content`}
          onClick={onToggle}
          className={`flex w-full items-center gap-3 p-4 text-left transition-colors hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--focus-ring-color)] sm:p-5 ${prominent ? "min-h-24" : "min-h-16"}`}
        >
          <IconWell size={prominent ? "md" : "sm"} aria-hidden>
            <Icon size={19} strokeWidth={LINE_ICON_STROKE} />
          </IconWell>
          <span className="min-w-0 flex-1 space-y-1">
            <Text as="span" variant={prominent ? "heading-sm" : undefined} weight="semibold" className="block">
              {title}
            </Text>
            <Text
              as="span"
              id={`${id}-description`}
              variant="body-sm"
              tone="muted"
              className="block"
            >
              {description}
            </Text>
          </span>
          <ChevronDown
            size={18}
            className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
          />
        </button>
      </Heading>
      <div
        id={`${id}-content`}
        role="region"
        aria-labelledby={`${id}-heading`}
        hidden={!open}
        className="border-t border-[var(--panel-border)] p-4 sm:p-5"
      >
        {children}
      </div>
    </Surface>
  );
}
