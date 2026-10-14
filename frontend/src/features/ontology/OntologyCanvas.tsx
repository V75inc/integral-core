import { useEffect, useImperativeHandle, useRef, forwardRef } from 'react';
import cytoscape, { type Core, type Stylesheet } from 'cytoscape';
import type { OntologyGraph } from '../../api/ontology';
import { NODE_NAMES } from './labels';

export interface OntologyCanvasHandle {
  fit: () => void;
  zoom: (factor: number) => void;
}

const EDGE_NAMES: Record<string, string> = {
  CONTAINS: 'contains', CATALOGS: 'catalogs', HAS_OPERATIONAL_MODEL: 'uses model',
};

function graphStyle(container: HTMLElement): Stylesheet[] {
  const css = getComputedStyle(container);
  const token = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const text = token('--text', '#26354a');
  const border = token('--text-subtle', '#8a95a5');
  const panel = token('--panel', '#ffffff');
  const accent = token('--brand-accent', '#4767ce');
  return [
    { selector: 'node', style: {
      label: 'data(label)', 'background-color': panel, 'border-color': border,
      'border-width': 1.5, color: text, width: 160, height: 64,
      shape: 'round-rectangle', 'font-size': 14, 'font-weight': 600, 'font-family': css.fontFamily,
      'text-valign': 'center', 'text-halign': 'center', 'text-wrap': 'wrap',
      'text-max-width': '140px', 'text-margin-y': -6, 'overlay-opacity': 0,
    } },
    { selector: 'node[entity = "Workspace"]', style: { 'border-color': accent, 'border-width': 3, width: 180, height: 70 } },
    { selector: 'node[entity = "WorkspaceApp"]', style: { 'border-color': accent, 'border-width': 2.5 } },
    { selector: 'node[entity = "OperationalModel"]', style: { shape: 'hexagon', 'border-style': 'dashed' } },
    { selector: 'node[entity = "EntryType"]', style: { shape: 'round-tag' } },
    { selector: 'node[entity = "Apps"], node[entity = "Tracks"]', style: { width: 130, height: 52, shape: 'ellipse' } },
    { selector: 'edge', style: {
      label: 'data(label)', 'curve-style': 'bezier', 'target-arrow-shape': 'triangle',
      'line-color': border, 'target-arrow-color': border, width: 1.2,
      color: token('--text-muted', '#606f82'), 'font-size': 9,
      'text-background-color': panel, 'text-background-opacity': 1, 'text-background-padding': '3px',
      'text-rotation': 'autorotate', 'overlay-opacity': 0,
    } },
    { selector: 'node:selected', style: { 'border-color': accent, 'border-width': 4 } },
    { selector: 'node.ontology-type', style: {
      label: 'data(label)', 'font-size': 10, 'font-weight': 400, color: token('--text-muted', '#606f82'),
      'background-opacity': 0, 'border-width': 0, width: 1, height: 1,
      'text-margin-y': 0, 'text-wrap': 'none', 'text-events': 'yes',
    } },
  ];
}

export const OntologyCanvas = forwardRef<OntologyCanvasHandle, {
  graph: OntologyGraph;
  selectedId: string | null;
  layout: 'breadthfirst' | 'circle';
  onSelect: (id: string) => void;
}>(function OntologyCanvas({ graph, selectedId, layout, onSelect }, ref) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useImperativeHandle(ref, () => ({
    fit: () => cyRef.current?.fit(undefined, 35),
    zoom: (factor) => {
      const cy = cyRef.current;
      if (cy) cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
    },
  }), []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const cy = cytoscape({
      container,
      elements: [
        ...graph.nodes.map(node => ({ data: { id: node.id, entity: node.entity, label: node.label }, classes: 'ontology-entity' })),
        ...graph.edges.map(edge => ({ data: { ...edge, label: EDGE_NAMES[edge.entity] ?? edge.entity } })),
      ],
      style: graphStyle(container),
      minZoom: 0.15, maxZoom: 3,
      layout: { name: layout, padding: 35, animate: false, directed: false, spacingFactor: 1.3, roots: [graph.meta.root_id] },
    });
    // Separate text nodes let type captions have their own typography. They
    // follow the entity's position and never participate in graph layout.
    cy.add(graph.nodes.map(node => {
      const entity = cy.getElementById(node.id);
      const position = entity.position();
      const labelBox = entity.boundingBox({ includeNodes: false, includeEdges: false, includeLabels: true });
      return {
        data: { id: `ontology-type:${node.id}`, ownerId: node.id, label: NODE_NAMES[node.entity] ?? node.entity },
        classes: 'ontology-type', position: { x: position.x, y: labelBox.y2 + 7 },
        selectable: false, grabbable: false,
      };
    }));
    cy.on('position', 'node.ontology-entity', event => {
      const node = event.target;
      const position = node.position();
      const labelBox = node.boundingBox({ includeNodes: false, includeEdges: false, includeLabels: true });
      cy.getElementById(`ontology-type:${node.id()}`).position({ x: position.x, y: labelBox.y2 + 7 });
    });
    cyRef.current = cy;
    cy.on('tap', 'node', event => onSelectRef.current(event.target.data('ownerId') ?? event.target.id()));
    const resize = new ResizeObserver(() => { cy.resize(); cy.fit(undefined, 35); });
    resize.observe(container);
    const theme = new MutationObserver(() => cy.style(graphStyle(container)));
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });
    return () => { resize.disconnect(); theme.disconnect(); cy.destroy(); cyRef.current = null; };
  }, [graph, layout]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedId) cy.getElementById(selectedId).select();
  }, [selectedId, graph, layout]);

  return <div ref={containerRef} role="img" aria-label="Workspace ontology graph. Use the visible nodes list to explore with a keyboard." className="w-full h-[430px] md:h-[550px]" />;
});
