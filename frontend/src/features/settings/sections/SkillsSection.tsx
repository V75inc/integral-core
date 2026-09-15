import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Lock, Search } from 'lucide-react';

import { skillsApi, type SkillSummary } from '../../../api/skills';
import { useScope } from '../../../context/ScopeContext';
import { Badge } from '../../../components/ui/Badge';
import { Button } from '../../../components/ui/Button';
import { EmptyState } from '../../../components/ui/EmptyState';
import { Skeleton } from '../../../components/ui/Skeleton';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '../../../components/ui/collapsible';
import { Input, Stack, Surface, Text } from '../../../ui';
import { SettingsSection } from '../components/Field';
import { SkillEditorModal, skillBadges } from '../../../components/skills/SkillEditorModal';
import { useToast } from '../../../context/ToastContext';

function skillsQueryKey(workspaceId: string | undefined) {
  return ['skills', workspaceId] as const;
}

const BADGE_VARIANT: Record<string, string> = {
  Core: 'info',
  Customized: 'ai',
  'Default updated': 'warning',
  Disabled: 'default',
  'Code skill': 'default',
};

function matchesQuery(skill: SkillSummary, q: string): boolean {
  if (!q) return true;
  const hay = [skill.name, skill.description, skill.key, skill.namespaced_name]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return hay.includes(q);
}

function SkillRow({
  skill,
  onOpen,
  onToggle,
  canManage,
}: {
  skill: SkillSummary;
  onOpen: () => void;
  onToggle?: () => void;
  canManage: boolean;
}) {
  const badges = skillBadges(skill);
  return (
    <Surface
      tone="panel-2"
      border="subtle"
      radius="card"
      className="flex items-center justify-between gap-3 px-4 py-3 transition hover:border-[var(--panel-border)]"
    >
      <button
        type="button"
        className="min-w-0 flex-1 text-left"
        onClick={onOpen}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Text as="span" variant="body" weight="medium" truncate>
            {skill.name}
          </Text>
          {skill.source === 'workspace' && skill.app_id ? (
            <Badge variant="info">{skill.app_slug || 'app-scoped'}</Badge>
          ) : null}
          {badges.map(b => (
            <Badge key={b} variant={BADGE_VARIANT[b] ?? 'default'}>
              {b === 'Core' ? <Lock size={10} className="mr-1" /> : null}
              {b}
            </Badge>
          ))}
        </div>
        <Text as="p" variant="body-sm" tone="muted" className="mt-1 line-clamp-1">
          {skill.description || skill.namespaced_name || skill.key}
        </Text>
      </button>
      {canManage && skill.source !== 'core' && onToggle ? (
        <button
          type="button"
          role="switch"
          aria-checked={skill.enabled}
          aria-label={skill.enabled ? 'Disable skill' : 'Enable skill'}
          onClick={onToggle}
          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors duration-fast focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring-color)] ${
            skill.enabled ? 'bg-[var(--brand-accent)]' : 'bg-[var(--badge-muted-bg)]'
          }`}
        >
          <span
            aria-hidden
            className={`inline-block h-4 w-4 rounded-full bg-white shadow-[var(--shadow-sm)] transition-transform duration-fast ${
              skill.enabled ? 'translate-x-[18px]' : 'translate-x-[2px]'
            }`}
          />
        </button>
      ) : null}
    </Surface>
  );
}

function GroupHeading({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center gap-2">
      <Text as="h3" variant="meta" weight="semibold" className="uppercase tracking-wide">
        {title}
      </Text>
      <Badge variant="default">{count}</Badge>
    </div>
  );
}

function SkillGroup({
  title,
  skills,
  onOpen,
  onToggle,
  canManage,
}: {
  title: string;
  skills: SkillSummary[];
  onOpen: (id: string) => void;
  onToggle: (skill: SkillSummary) => void;
  canManage: boolean;
}) {
  if (skills.length === 0) return null;
  return (
    <div className="flex flex-col gap-2">
      <GroupHeading title={title} count={skills.length} />
      <Stack gap="sm">
        {skills.map(skill => (
          <SkillRow
            key={skill.id}
            skill={skill}
            onOpen={() => onOpen(skill.id)}
            onToggle={() => onToggle(skill)}
            canManage={canManage}
          />
        ))}
      </Stack>
    </div>
  );
}

export function SkillsSection() {
  const { scope, activeWorkspace, isPersonal } = useScope();
  const workspaceId = scope?.workspaceId;
  const toast = useToast();
  const qc = useQueryClient();
  const [editorId, setEditorId] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // Personal workspace = owner. Org workspace = admin/owner only (mirrors the
  // backend workspace-admin gate on skill writes).
  const canManage =
    isPersonal || activeWorkspace?.your_role === 'admin' || activeWorkspace?.your_role === 'owner';

  const { data, isLoading, isError } = useQuery({
    queryKey: skillsQueryKey(workspaceId),
    queryFn: () => skillsApi.list(),
    enabled: Boolean(workspaceId),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      skillsApi.update(id, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: skillsQueryKey(workspaceId) });
    },
    onError: () => toast.showToast('Failed to update skill', 'error'),
  });

  const onToggle = (skill: SkillSummary) =>
    toggleMutation.mutate({ id: skill.id, enabled: !skill.enabled });

  const q = query.trim().toLowerCase();

  const workspaceSkills = useMemo(
    () => (data?.workspace || []).filter(s => matchesQuery(s, q)),
    [data?.workspace, q],
  );

  const coreSkills = useMemo(
    () => (data?.core || []).filter(s => matchesQuery(s, q)),
    [data?.core, q],
  );

  const appGroups = useMemo(() => {
    const byApp = new Map<string, SkillSummary[]>();
    for (const skill of data?.apps || []) {
      if (!matchesQuery(skill, q)) continue;
      const slug = skill.app_slug || 'app';
      const list = byApp.get(slug) || [];
      list.push(skill);
      byApp.set(slug, list);
    }
    return [...byApp.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [data?.apps, q]);

  const appMatchCount = appGroups.reduce((n, [, s]) => n + s.length, 0);
  const filteredTotal = workspaceSkills.length + appMatchCount + coreSkills.length;
  // A live search auto-reveals matching core skills so hits aren't hidden
  // behind the collapsed Advanced section.
  const coreOpen = advancedOpen || (q.length > 0 && coreSkills.length > 0);

  return (
    <SettingsSection
      title="AI Skills"
      description="View and customize the skills your workspace agent can use. Core skills are built in and read-only; app and workspace skills can be tailored to your workspace."
      actions={
        canManage ? (
          <Button onClick={() => setEditorId('__new__')}>New skill</Button>
        ) : undefined
      }
    >
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : isError ? (
        <EmptyState title="Could not load skills" />
      ) : (
        <Stack gap="lg">
          <div className="relative">
            <Text
              as="span"
              tone="muted"
              className="pointer-events-none absolute left-3 top-1/2 flex -translate-y-1/2"
            >
              <Search size={15} />
            </Text>
            <Input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search skills…"
              aria-label="Search skills"
              style={{ paddingLeft: '2.25rem' }}
            />
          </div>

          {(data?.total || 0) === 0 ? (
            <EmptyState title="No skills in this workspace yet" />
          ) : filteredTotal === 0 ? (
            <EmptyState title={`No skills match “${query.trim()}”`} />
          ) : (
            <Stack gap="lg">
              <SkillGroup
                title="Workspace skills"
                skills={workspaceSkills}
                onOpen={setEditorId}
                onToggle={onToggle}
                canManage={canManage}
              />
              {appGroups.map(([slug, skills]) => (
                <SkillGroup
                  key={slug}
                  title={slug}
                  skills={skills}
                  onOpen={setEditorId}
                  onToggle={onToggle}
                  canManage={canManage}
                />
              ))}

              {coreSkills.length > 0 ? (
                <Collapsible open={coreOpen} onOpenChange={setAdvancedOpen}>
                  {/* CollapsibleTrigger is a real Radix button — routing it
                      through <Surface as="button"> would drop the onClick/
                      aria-expanded/data-state props Radix's asChild clones
                      onto the child, since Surface doesn't forward rest
                      props. Nesting the trigger inside a Surface instead
                      keeps Radix's native wiring intact and still gets the
                      typed background/border. */}
                  <Surface
                    tone="panel-2"
                    border="subtle"
                    radius="card"
                    className="transition hover:border-[var(--panel-border)]"
                  >
                    <CollapsibleTrigger className="group flex w-full items-center justify-between gap-2 px-4 py-3 text-left">
                      <span className="flex items-center gap-2">
                        <Text as="span" tone="muted">
                          <Lock size={14} />
                        </Text>
                        <Text as="span" variant="body" weight="medium">
                          Advanced · Core skills
                        </Text>
                        <Badge variant="default">{coreSkills.length}</Badge>
                      </span>
                      <Text
                        as="span"
                        tone="muted"
                        className="flex transition-transform group-data-[state=open]:rotate-180"
                      >
                        <ChevronDown size={16} />
                      </Text>
                    </CollapsibleTrigger>
                  </Surface>
                  <CollapsibleContent className="pt-2">
                    <Stack gap="sm">
                      <Text as="p" variant="body-sm" tone="muted" className="px-1">
                        Built-in skills that power the agent. These are read-only.
                      </Text>
                      {coreSkills.map(skill => (
                        <SkillRow
                          key={skill.id}
                          skill={skill}
                          onOpen={() => setEditorId(skill.id)}
                          canManage={canManage}
                        />
                      ))}
                    </Stack>
                  </CollapsibleContent>
                </Collapsible>
              ) : null}
            </Stack>
          )}
        </Stack>
      )}

      <SkillEditorModal
        open={editorId !== null}
        skillId={editorId}
        canManage={canManage}
        onClose={() => setEditorId(null)}
        onSaved={() => qc.invalidateQueries({ queryKey: skillsQueryKey(workspaceId) })}
      />
    </SettingsSection>
  );
}
