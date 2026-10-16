import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Expand, Network, RefreshCw, Search, ZoomIn, ZoomOut } from 'lucide-react';
import { ontologyApi, type OntologyGraph, type OntologyRoot } from '../api/ontology';
import { useAuth } from '../context/AuthContext';
import { useScope } from '../context/ScopeContext';
import { useSetCrumbs } from '../context/CrumbsContext';
import { Button, PageHeading, PageSection, PageShell } from '../components/ui';
import { Input, Select, Surface, Text } from '../ui';
import { OntologyCanvas, type OntologyCanvasHandle } from '../features/ontology/OntologyCanvas';
import { NODE_NAMES } from '../features/ontology/labels';

function destination(entity: string, id: string): string | null {
  if (entity === 'WorkspaceApp') return `/apps/${encodeURIComponent(id)}`;
  if (entity === 'Track') return `/tracks/${encodeURIComponent(id)}`;
  return null;
}

export function OntologyPage() {
  const { user } = useAuth();
  const { scope, activeWorkspace } = useScope();
  const workspaceId = scope?.workspaceId;
  useSetCrumbs([{ label: 'Ontology' }]);
  return <PageShell>
    <PageSection>
      <PageHeading>Ontology</PageHeading>
      <Text as="p" tone="muted" className="mt-2">Explore how {activeWorkspace?.name || 'your workspace'} is structured. Only what you can see appears here.</Text>
    </PageSection>
    <PageSection className="mt-6">
      {workspaceId && user ? <WorkspaceOntology key={`${user.id}:${workspaceId}`} workspaceId={workspaceId} /> : <Text tone="muted">Choose a workspace to explore.</Text>}
    </PageSection>
  </PageShell>;
}

function WorkspaceOntology({ workspaceId }: { workspaceId: string }) {
  const [root, setRoot] = useState<string | null>(null);
  const [history, setHistory] = useState<(string | null)[]>([]);
  const [depth, setDepth] = useState(3);
  const [layout, setLayout] = useState<'breadthfirst' | 'circle'>('breadthfirst');
  const [capture, setCapture] = useState<{ key: string; graph: OntologyGraph | null; error: string | null } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [search, setSearch] = useState('');
  const [roots, setRoots] = useState<OntologyRoot[] | null>(null);
  const [rootsKey, setRootsKey] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const canvasRef = useRef<OntologyCanvasHandle>(null);
  const searchAbort = useRef<AbortController | null>(null);
  const captureKey = `${workspaceId}:${root}:${depth}:${refresh}`;
  const loading = capture?.key !== captureKey;
  const graph = !loading ? capture?.graph ?? null : null;
  const error = !loading ? capture?.error ?? null : null;

  useEffect(() => {
    const controller = new AbortController();
    ontologyApi.capture(workspaceId, root, depth, controller.signal).then(next => {
      if (controller.signal.aborted) return;
      if (next.meta.workspace_id !== workspaceId) throw new Error('Unexpected workspace');
      setCapture({ key: captureKey, graph: next, error: null });
      setSelectedId(previous => next.nodes.some(node => node.id === previous) ? previous : next.meta.root_id);
    }).catch(() => {
      if (!controller.signal.aborted) setCapture({ key: captureKey, graph: null, error: 'This view is unavailable. Access may have changed; try the workspace view or another starting point.' });
    });
    return () => controller.abort();
  }, [workspaceId, root, depth, captureKey]);

  useEffect(() => () => searchAbort.current?.abort(), []);

  // A focus return is a new live capture, not a replay of a cached graph.
  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState === 'visible') setRefresh(value => value + 1);
    };
    window.addEventListener('focus', refreshVisible);
    const interval = window.setInterval(refreshVisible, 60_000);
    return () => { window.removeEventListener('focus', refreshVisible); window.clearInterval(interval); };
  }, []);

  const browse = useCallback((id: string | null) => {
    searchAbort.current?.abort();
    setFinding(false);
    setHistory(previous => [...previous, root]);
    setRoot(id);
    setFilter('');
    setRoots(null);
    setRefresh(value => value + 1);
  }, [root]);

  const findRoots = async () => {
    searchAbort.current?.abort();
    const controller = new AbortController();
    searchAbort.current = controller;
    setFinding(true); setSearchError(null); setRoots(null);
    try {
      const next = await ontologyApi.roots(workspaceId, search, controller.signal);
      if (!controller.signal.aborted) { setRoots(next); setRootsKey(captureKey); }
    } catch {
      if (!controller.signal.aborted) setSearchError('Starting points are unavailable. Try a narrower search.');
    } finally { if (!controller.signal.aborted) setFinding(false); }
  };

  const selected = graph?.nodes.find(node => node.id === selectedId);
  const visibleNodes = useMemo(() => graph?.nodes.filter(node => `${node.label} ${NODE_NAMES[node.entity] ?? node.entity}`.toLowerCase().includes(filter.toLowerCase())) ?? [], [graph, filter]);
  const target = selected ? destination(selected.entity, selected.id) : null;

  return <div className="space-y-4">
    <Surface padding="md">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" icon={<ArrowLeft size={15} />} disabled={!history.length || loading} onClick={() => {
          const previous = history[history.length - 1];
          setHistory(history.slice(0, -1)); setRoot(previous); setRefresh(value => value + 1);
        }}>Back</Button>
        <Button variant="secondary" size="sm" onClick={() => { setHistory([]); setRoot(null); setRefresh(value => value + 1); }}>Workspace</Button>
        <label className="flex items-center gap-2">
          <Text variant="meta">Steps</Text>
          <Select aria-label="Graph depth" value={depth} onChange={event => setDepth(Number(event.target.value))}>
            {[1, 2, 3, 4].map(value => <option key={value} value={value}>{value}</option>)}
          </Select>
        </label>
        <div className="w-40"><Select aria-label="Graph layout" value={layout} onChange={event => setLayout(event.target.value as typeof layout)}>
          <option value="breadthfirst">Hierarchy</option><option value="circle">Circle</option>
        </Select></div>
        <div className="flex gap-1 ml-auto">
          <Button variant="ghost" size="sm" aria-label="Zoom out" icon={<ZoomOut size={16} />} disabled={!graph} onClick={() => canvasRef.current?.zoom(0.8)} />
          <Button variant="ghost" size="sm" aria-label="Zoom in" icon={<ZoomIn size={16} />} disabled={!graph} onClick={() => canvasRef.current?.zoom(1.25)} />
          <Button variant="ghost" size="sm" aria-label="Fit graph" icon={<Expand size={16} />} disabled={!graph} onClick={() => canvasRef.current?.fit()} />
          <Button variant="ghost" size="sm" aria-label="Refresh ontology" icon={<RefreshCw size={16} />} loading={loading} onClick={() => setRefresh(value => value + 1)} />
        </div>
      </div>
      <form className="flex gap-2 mt-3" onSubmit={event => { event.preventDefault(); void findRoots(); }}>
        <Input aria-label="Find a starting point" placeholder="Find an app or track to browse from…" maxLength={128} value={search} onChange={event => setSearch(event.target.value)} className="min-w-0 flex-1" />
        <Button variant="secondary" size="sm" icon={<Search size={15} />} loading={finding} type="submit">Find</Button>
      </form>
      {searchError && <Text as="p" tone="danger" className="mt-3">{searchError}</Text>}
      {roots && rootsKey === captureKey && !loading && !error && <div className="mt-3 max-h-48 overflow-auto" aria-label="Starting points">
        {roots.length ? roots.map(item => <Button key={item.id} variant="ghost" size="sm" className="w-full justify-start" onClick={() => browse(item.id)}>{item.label} · {NODE_NAMES[item.entity]}</Button>) : <Text tone="muted">No readable starting points found. Try a more specific name.</Text>}
      </div>}
    </Surface>

    {error && <Surface padding="lg"><div role="alert" className="space-y-3"><Text as="p" tone="danger">{error}</Text><Button variant="secondary" onClick={() => browse(null)}>Return to workspace</Button></div></Surface>}
    {loading && <Surface padding="lg"><div role="status" className="flex items-center gap-3 min-h-[300px] justify-center"><Network size={24} /><Text tone="muted">Loading your visible ontology…</Text></div></Surface>}
    {graph && !loading && <>
      <div className="flex flex-wrap items-center gap-2" role="status">
        <Text variant="meta">{graph.nodes.length} visible nodes · {graph.edges.length} relationships · within {depth} {depth === 1 ? 'step' : 'steps'}</Text>
        {!graph.meta.complete && <Text variant="meta" tone="muted">Partial view — browse from a node to explore further.</Text>}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_280px] gap-4">
        <Surface className="min-w-0 overflow-hidden">
          <OntologyCanvas ref={canvasRef} graph={graph} layout={layout} selectedId={selectedId} onSelect={setSelectedId} />
          <div className="px-4 pb-4"><Text variant="meta">Drag to pan. Scroll or use the controls to zoom. Select a node to inspect it.</Text></div>
        </Surface>
        <div className="space-y-4 min-w-0">
          <Surface padding="lg">
            {selected ? <div className="space-y-3">
              <Text variant="meta">{NODE_NAMES[selected.entity] ?? selected.entity}</Text>
              <Text variant="heading-sm" as="h2" className="break-words">{selected.label}</Text>
              {Boolean(selected.context.description || selected.context.purpose) && <Text as="p" tone="muted">{selected.context.description || selected.context.purpose}</Text>}
              {selected.context.version_number && <Text as="p" variant="meta">Version {selected.context.version_number}</Text>}
              <Button variant="secondary" size="sm" disabled={selected.id === graph.meta.root_id} onClick={() => browse(selected.id)}>Browse from here</Button>
              {target && <Link to={target} className="block underline underline-offset-4"><Text variant="meta">Open {NODE_NAMES[selected.entity].toLowerCase()}</Text></Link>}
              {selected.schema_fields && <div className="space-y-2">
                <Text as="h3" variant="label">Fields</Text>
                {selected.schema_fields.length ? selected.schema_fields.map(field => <div key={field.key} className="flex justify-between gap-2"><Text variant="meta" className="break-words">{field.label}{field.required ? ' *' : ''}</Text><Text variant="meta" tone="muted">{field.type}</Text></div>) : <Text variant="meta" tone="muted">No visible field declarations.</Text>}
                {selected.schema_fields_limited && <Text as="p" variant="meta" tone="muted">Field preview is bounded. Browse from this entry type for a closer view.</Text>}
              </div>}
            </div> : <Text tone="muted">Select a node to inspect it.</Text>}
          </Surface>
          <Surface padding="md">
            <div className="space-y-3">
              <Text as="h2" variant="label">Visible nodes</Text>
              <Input aria-label="Filter visible nodes" placeholder="Filter this view…" value={filter} onChange={event => setFilter(event.target.value)} />
              <div className="max-h-64 overflow-auto space-y-1" aria-label="Visible nodes">
                {visibleNodes.map(node => <Button key={node.id} variant={selectedId === node.id ? 'secondary' : 'ghost'} size="sm" className="w-full justify-start" aria-pressed={selectedId === node.id} onClick={() => setSelectedId(node.id)}><span className="truncate">{node.label} · {NODE_NAMES[node.entity]}</span></Button>)}
                {!visibleNodes.length && <Text variant="meta" tone="muted">No matching visible nodes.</Text>}
              </div>
            </div>
          </Surface>
        </div>
      </div>
      {graph.nodes.every(node => ['Workspace', 'Apps', 'Tracks'].includes(node.entity)) && <Text as="p" tone="muted">No readable apps or tracks in this neighborhood yet. Use Find to look for a shared starting point.</Text>}
    </>}
  </div>;
}
