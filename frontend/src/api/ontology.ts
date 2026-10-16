import apiClient from './client';

export interface OntologyField {
  key: string;
  label: string;
  type: string;
  required: boolean;
}

export interface OntologyNode {
  id: string;
  entity: string;
  label: string;
  context: Record<string, string | number>;
  schema_fields?: OntologyField[];
  schema_fields_limited?: boolean;
}

export interface OntologyRoot {
  id: string;
  entity: string;
  label: string;
}

export interface OntologyGraph {
  nodes: OntologyNode[];
  edges: { id: string; entity: string; source: string; target: string }[];
  meta: {
    workspace_id: string;
    root_id: string;
    max_depth: number;
    complete: boolean;
    captured_at: string;
  };
}

export const ontologyApi = {
  capture: async (workspaceId: string, rootId: string | null, depth: number, signal: AbortSignal): Promise<OntologyGraph> => {
    const { data } = await apiClient.post('/workspace-ontology', {
      root_id: rootId,
      max_depth: depth,
    }, { signal, headers: { 'X-Integral-Scope': `ws:${workspaceId}` } });
    return data;
  },
  roots: async (workspaceId: string, search: string, signal: AbortSignal): Promise<OntologyRoot[]> => {
    const { data } = await apiClient.get('/workspace-ontology/roots', {
      signal,
      params: { search },
      headers: { 'X-Integral-Scope': `ws:${workspaceId}` },
    });
    return data.roots;
  },
};
