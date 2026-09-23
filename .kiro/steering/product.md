# Product Overview

**Integral** is an AI-native knowledge platform built on graph substrate principles. It serves as a conformable application substrate that captures, organizes, and exposes domain knowledge for both humans and AI agents operating under a unified access model.

## Core Mission

Remove the fundamental impediment to AI-first operation: agents have no canonical place to read from or write to. Integral collapses fragmented domain knowledge into a single graph with:

- Flexible OperationalModel schema layer
- Agentive uplink registry (per-user, org-facing, BYOA)
- First-class support for human-to-human and human-to-AI collaboration (A2A fabric retired — external agents coordinate via MCP on the shared substrate)

## Platform Architecture

Built on **jvspatial** (graph substrate) with:
- **Frontend**: React 18 + TypeScript (port 9006)
- **Backend**: Python FastAPI (port 4000)
- **Graph-based data model**: All entities are Nodes with explicit Edges

## Core Primitives

```
Workspace → App → Track → Entry
```

- **Workspace**: Top-level operational environment (personal or organization)
- **App**: Coherent operational domain bundling Tracks, Skills, Agents
- **Track**: Table-like collection of Entries (≈ database table)
- **Entry**: Individual records with typed fields (≈ table row)
- **OperationalModel**: Declarative schema manifest defining structure

## Key Features

- **Apps as Bundles**: YAML + prompts + domain understanding, not engineering
- **Flexible Schema**: OperationalModel system for dynamic typing
- **Graph Substrate**: jvspatial object-spatial framework
- **Agentive Layer**: Always-on ops layer on a pluggable harness (MCP + staging + skills); Harness Switcher selects provider
- **Access Model**: Workspace-gated with cascade and explicit deny
- **Sharing**: Collaborators, share links, invitations at all levels

## Product Status

Pre-1.0 architecture (provisional). Active development focused on substrate stability and core primitives before expanding agent surfaces.
