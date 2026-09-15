# Handoff: Integral · Fish-eye Graph Navigation

## Overview
A graph-based, fish-eye navigation surface for "Integral" — a workspace tool whose units (workspaces, spaces, tracks, entries, people) are nodes connected by edges. The user drills through the graph one focus at a time. The current focus sits in the middle of the screen at full size; its direct neighbors orbit around it on a concentric ring; its parent is partially visible off the left edge so the user can always backtrace one level. Drilling animates as a continuous orbital sweep — the universe rotates the new focus into the center while children swing into orbit around it. A side panel slides in from the right with type-aware content (entries/comments/members for tracks, activity for people, etc.).

## About the Design Files
The files in this bundle are **design references created in HTML** — a prototype demonstrating the intended look and behavior, not production code to ship as-is. The task is to **recreate this design in the target codebase's existing environment** (React, Vue, SwiftUI, native, etc.) using that codebase's established patterns, component primitives, and design tokens. If no codebase environment exists yet, pick the framework most appropriate for the product (React + an animation library like Framer Motion is a natural fit) and implement there.

The prototype is split across five files for editability — a real implementation would reorganize this into proper modules/components per the host codebase's conventions.

## Fidelity
**High-fidelity.** The prototype uses final motion timings, easing curves, sizing math, color values, and typographic scale. Recreate pixel-perfectly using the codebase's existing primitives — but do match the exact values listed in the **Design Tokens** and **Motion** sections below.

## Architecture
The design is a single full-viewport canvas with a side panel; there are no "screens" in the traditional sense — the same canvas re-renders as the focus changes.

### Core state (single source of truth)
- `focusId: string` — the currently centered node's id.
- `panelOpen: boolean` — whether the side panel is visible.
- `rotOffset: number` — orbital ring rotation offset in degrees, used for the infinite-scroll mechanism.
- `layout: Record<NodeId, LayoutEntry>` — the current rendered position/scale/opacity for every node, computed from the above.
  - `LayoutEntry = { x, y, scale, opacity, ring, isParent, angleDeg }`
  - `ring`: 0 = focus, 1 = direct neighbor on orbital ring, 2 = peripheral grandchild, -1 = hidden.

### Layout function
`computeLayout(focusId, viewportW, viewportH, panelOpen, rotOffset) -> layout`. Pure function. The renderer always asks "given the current state, where does each node sit?" The same function is used for static placement, drag-rotate updates, and as the target of every animated transition.

Geometry, in order:
1. **Effective width** = `viewportW - panelWidthFor(viewportW)` if `panelOpen`, else full width. `panelWidthFor` = `clamp(560px, 50vw, 820px)`. The center of the universe shifts left when the panel opens.
2. **Center** `(cx, cy)` = `(effW/2, viewportH/2 + 4)`. The +4 is a tiny baseline correction.
3. **Ring radii** — `R1 = min(310, dim*0.34)`, `R2 = min(560, dim*0.62)`, where `dim = min(effW, viewportH)`.
4. **Focus** placed at `(cx, cy)` with `scale = 1.0`.
5. **Parent corner** — `PARENT[focusId]`, if any — placed at viewport-anchored `(38, cy - 60)` with `scale = 0.78`. **Anchored to viewport, NOT effective area** — the parent is intentionally clipped on the left edge by ~30%, and stays in place when the panel opens (universe slides past it). This is the always-visible "back" affordance.
6. **Ring 1 children** — direct neighbors of focus, excluding parent. Distributed evenly around 360° starting from -90° (top), then offset by `rotOffset`. Adaptive scale: `min(0.46, 2*R1*sin(π/N) / FOCUS_PX / 1.18)` clamped to `[0.26, 0.46]`, so children never overlap on small viewports. `FOCUS_PX = 260` is the reference size of a `scale=1` node.
7. **Hidden zone** — a ~50° arc centered at angle 180° (behind the parent) where ring-1 children fade out. When `N > 12`, the hidden arc widens to `360 * (N-12)/N + 18°` so only 12 are ever fully visible at once. There's also a soft falloff (~22°-60° from 180°) when `N <= 12` so the parent partially occludes the children passing behind it.
8. **Ring 2 grandchildren** — for each placed ring-1 child, up to 2 of its OTHER neighbors (not focus, not its own parent) are placed at radius R2 along the same angle, fanned ±12°. Capped at 8 total. Skipped entirely when ring 1 is crowded (N > 8).
9. Nodes not placed on rings 0/1/2 are pushed to opacity 0 at the center.

### Edges
Render as 1px SVG lines between layout positions. **Critical:** only edges *incident to the current focus* render — parent→focus and focus→child. Cross-track and grandchild edges are explicitly hidden. This keeps the graph reading as a clean center-and-spokes per drill level, not a tangled web.

Edge styling:
- Parent edge: `rgba(255, 220, 195, 0.32 * opacity)`, 1.2px, solid.
- Focus-to-child edges: `rgba(220, 220, 235, 0.30 * opacity)`, 1.05px, solid.
- (Peripheral/non-focus edges, currently filtered out, used dashed styling — kept in code for future re-enable.)

## Interactions & Behavior

### Drill in (click a child node)
1. `focusId` ← clicked node id; `rotOffset` ← 0; `panelOpen` ← `true` (auto-opens on first drill, persists thereafter).
2. Compute new target layout.
3. **Reconcile** old vs new layouts (see below) so appearing/disappearing nodes ride along graph edges.
4. **Animated transition** runs over **760ms** with `easeInOutCubic`. Per-frame:
   - Lerp every node's `{x, y, scale, opacity}` from old → new layout.
   - **Orbital spin overlay**: rotate the *new focus's direct children only* (those landing on ring 1, not the parent) by `90° * (1 - eased)` around the new focus's lerped position. This makes children swing in along arcs rather than translating in straight lines — the entry IS the orbit. Exiting nodes, parent corner, and ring-2 peripherals keep clean straight-line lerps.

### Drill up (click parent corner, breadcrumb, or press Esc)
Same as drill-in but with `spinDeg = -75°` (counter-rotation, so the perceived "back" sweeps the new ring opposite to drill-in). Panel state preserved (does not auto-close on drill-up).

### Layout reconcile (for transitions)
For each node, when comparing from-layout to to-layout:
- **Appearing** (was hidden, will be visible): in the *from* layout, place it at the position of a graph-neighbor that IS visible in from-layout (or the from-focus). Scale 0.45×, opacity 0. So it visually "flies out from" a relative as it appears.
- **Disappearing** (was visible, will be hidden): in the *to* layout, place it at the position of a graph-neighbor that IS visible in to-layout. So it visually "flies into" a relative as it leaves.

This is what makes the motion feel continuous and edge-respecting — nodes slide along graph relationships rather than fading into space.

### Ring rotation (infinite scroll)
- **Drag**: any pointer-down on the stage (not on a node) starts a rotation drag. `rotOffset += dx * 0.4` per pixel of horizontal movement. Direct manipulation — no animation; layout updates synchronously.
- **Scroll wheel**: native non-passive `wheel` listener on the stage `preventDefault`s the page scroll and applies `rotOffset += deltaY * 0.28` (or `deltaX` if larger).
- **Arrow keys / chevrons**: ←/→ key or visible chevron buttons step `rotOffset` by ±30°.
- During drag, `dragRef.moved` accumulates. If <4px on pointer-up, treat as a click; otherwise swallow.

### Side panel
- Slides in from the right via CSS `transform: translateX(100% → 0)`, `480ms cubic-bezier(0.22, 1, 0.36, 1)`.
- **Persists** across drills until manually closed via the × button or `P` key.
- Width: `clamp(560px, 50vw, 820px)`. The layout function uses the same width to shrink the universe's effective area.
- Content is **type-aware** — see Side Panel Content below.

### Keyboard
- `Esc` — drill up (to parent), if any.
- `P` — toggle panel.
- `←` / `→` — rotate ring by ±30°.
- (Inputs/textareas swallow these, naturally.)

### Click-on-focus
Clicking the centered focus node toggles the panel. Clicking any other node drills.

## Screens / Views

### 1. Stage (main canvas)
**Background:** `radial-gradient(ellipse 70% 55% at 50% 50%, #1c1c20 0%, #0e0e11 55%, #08080a 100%)` with an additional inner-vignette overlay (`radial-gradient ... transparent 55%, rgba(0,0,0,0.55) 100%`).

**Orbital guides:** two faint dashed circles (R1 dashed, R2 dotted) centered on focus, `rgba(255,255,255,0.04)` border, transitioned with the same 720ms ease.

### 2. Top chrome
Fixed top bar, 18px×28px padding, `pointer-events: none` on the bar with `auto` re-enabled on its children.

- **Wordmark (left):** "Integral" with an 18×18 rounded mark — `linear-gradient(135deg, #ff8552, #ff5a1f)`, 5px radius, 16px orange glow. Click → drill to root workspace.
- **Breadcrumb chain:** workspace › space › track › … chevrons in `--ink-4`. Each crumb is clickable → drill to that level. Active crumb in `--ink`, others in `--ink-3`, hover → `--ink`.
- **Right chrome:** search pill (`240px`, `rgba(255,255,255,0.025)`, `1px solid var(--line)`, 100px radius, "Search the universe" placeholder) + 28×28 round avatar with the same orange→magenta gradient.

### 3. Node sphere
Used at all sizes 0..1.

- Container: absolute, `border-radius: 50%`, `width = height = scale * 260px`, `transform: translate(-50%, -50%)`.
- Sphere fill: `radial-gradient(circle at 32% 28%, rgba(255,255,255,0.045) 0%, transparent 45%) over rgba(28,28,32,0.78)`. `backdrop-filter: blur(12px) saturate(120%)`.
- Border: `1px solid rgba(255,255,255,0.07)`.
- **Focus variant:** background swaps to `rgba(40,40,44,0.9)`; border to `rgba(255,255,255,0.1)`; outer pulse ring: `1px solid rgba(255,255,255,0.05)` at `inset: -3%`, `animation: pulse 4.6s ease-in-out infinite` (scale 1 → 1.07, opacity 0.55 → 0.18).
- **Parent-corner variant:** background `rgba(34,34,40,0.92)`; eyebrow gets a "↩ " prefix to read as "back".
- Hover (non-focus): background `rgba(46,46,52,0.9)`; border `rgba(255,255,255,0.16)`; outer glow `0 0 80px var(--node-glow)` (= 16% alpha of node color).

**Sphere content** (vertically centered):
- Tiny dot indicator top-right (12% from edge), 0.5em diameter, color = node color, 8px glow. Hidden on focus.
- Eyebrow (uppercase, 0.62em on small / 0.42em on focus, 0.13em letter-spacing, color = node color).
- Title (0.92em on small / 1.18em on focus, weight 600, `letter-spacing: -0.012em`, `text-wrap: balance`).
- Meta (0.62em on small / 0.5em on focus, color `--ink-3`). Hidden when scale < 0.36.

### 4. Rotation chevrons
Two 30px circular buttons positioned at `(cx ± R1 ± 22px, cy)`. Background `rgba(255,255,255,0.04)`, border `var(--line)`, color `var(--ink-2)`, blur 8px backdrop. Show only when `childCount > 3` so they don't appear pointless on small fanouts.

### 5. Bottom rail
Pill-shaped quick-jump rail centered at `bottom: 22px`. Background `rgba(20,20,22,0.72)`, blur 20px, 100px radius, 1px line border, 5px padding. Each button is 11px font, transparent → `rgba(255,255,255,0.04)` on hover, `rgba(255,255,255,0.07)` when active. Optional 6px color dot before the label.

### 6. Side panel
Fixed right, full-height. Background `rgba(15,15,18,0.94)`, 28px backdrop-blur with 140% saturation, 1px left border `var(--line)`, big drop shadow `-40px 0 80px rgba(0,0,0,0.5)`.

#### Header (28px×36px padding bottom 18, 1px bottom line)
- 11px uppercase eyebrow (kind label), color = `--panel-color` (= focused node's color).
- 26px weight-600 title, `letter-spacing: -0.022em`.
- 12px meta line in `--ink-3`.
- Close (×) at top-right: 30px round, hover `rgba(255,255,255,0.05)` background.
- Presence row: green dot (7px, 8px glow `#65d895`) + tiny avatar stack (18px circles, -6px overlap, 1.5px outer ring matching `--bg`) + "N here · Names" in 11px `--ink-2`.

#### Tabs (only for kind=track and kind=space)
Feed | Comments | Members | Settings. 12px font, 9px×12px padding, transparent until active. Active tab: `--ink` color, 2px bottom border = `--panel-color`.

#### Body (scrollable, 22px×36px, 32px bottom)
Section labels: 10px uppercase 0.12em letter-spacing `--ink-3`, 12px bottom margin.

**Entry card:** 1px line border, 10px radius, 13px×14px padding, `rgba(255,255,255,0.012)` background. Hover bumps both. Internal layout:
- Row 1: status pill (9.5px uppercase 700, 7px×3px padding, color = `entry.statusColor`, background = same color at 12% alpha) + title (13px weight 500).
- Row 2: 18px avatar + "Name · time" (11px `--ink-3`) + comment count right-aligned in `--ink-2`.

**Comment bubble:** 26px avatar + body. Body has head row (name 600 `--ink`, time `--ink-3`, both 11px) + body text 13px `--ink-2`, 1.45 line-height. 1px bottom-line separator between bubbles.

**Member row:** 32px avatar + name (13px 500) and meta (11px `--ink-3`).

#### Composer (sticky bottom, 14px×24px, 1px top line)
28px avatar + pill input (`rgba(255,255,255,0.03)` bg, 100px radius, 9px×14px, 12px font, focus → `rgba(255,255,255,0.05)` + brighter border) + 30px circular send button (`var(--panel-color)` fill, dark text, ↑ glyph).

## Side Panel Content (per kind)

| Kind | Tabs | Feed default content |
|-------------|-------------------------|---------------------------------------------------|
| `workspace` | (no tabs) | List of pulse activity rows ("CRM · 8 entries today") |
| `space` | Feed/Comments/Members/Settings | Pulse + recent events + alerts |
| `track` | Feed/Comments/Members/Settings | Entry cards (status, title, assignee, comments) |
| `person` | (no tabs) | Activity stream ("Just commented on …", "Closed …") |
| `entry` | (not implemented in prototype — same layout as track entries, single record) |

The mock data lives in `MOCK_ENTRIES` in `fisheye-data.js`. Replace with real data fetches keyed by `focusId`. The shape per item is `{title, status, statusColor, who: personId, when, comments}`.

Live presence is a stub: `PRESENCE` map of `id → personId[]`. Replace with realtime subscription.

## Motion / Animation Spec

| Transition | Duration | Easing | Notes |
|----------------------|----------|------------------------------------|----------------------------------------------------|
| Drill in/out | 760ms | `easeInOutCubic` | + orbital spin overlay (see below) |
| Panel open/close | 460ms | `easeInOutCubic` (universe lerp); `cubic-bezier(0.22, 1, 0.36, 1)` (panel slide) | No spin |
| Hover sphere | 220ms | ease | Background + border + shadow |
| Focus pulse | 4.6s | ease-in-out infinite | scale 1 → 1.07, opacity 0.55 → 0.18 |

**Orbital spin overlay (drill only):** during the lerp, rotate every TO-layout ring-1 child by `θ = ±90° * (1 - eased)` around the new focus's lerped position. Sign positive on drill-down, negative on drill-up. This makes children visually swing into orbit instead of translating linearly. Implementation in `runTransition()` in `fisheye-app.jsx`.

**Reconcile (appearing/disappearing nodes):** before lerping, rewrite from/to-layout entries for nodes that are visible on only one side, so they originate from / disappear into a graph-neighbor that's visible on the other side, at scale 0.45×. Edge-respecting motion.

## Design Tokens

### Colors
```css
--bg-deep: #08080a; /* page bg */
--bg: #0f0f12; /* panel inner */
--ink: #ededed; /* primary text */
--ink-2: #a3a3a3; /* secondary text */
--ink-3: #6b6b6b; /* tertiary text */
--ink-4: #45454a; /* disabled / separators */
--line: rgba(255, 255, 255, 0.06);
--line-2: rgba(255, 255, 255, 0.03);
--accent: #ff8552; /* brand orange */
--node-bg: rgba(28, 28, 32, 0.78);
--node-bg-focus: rgba(40, 40, 44, 0.9);
--node-bg-parent: rgba(34, 34, 40, 0.92);
--node-border: rgba(255, 255, 255, 0.07);
--panel-bg: rgba(15, 15, 18, 0.94);
```

### Per-node colors (sample data)
Workspace/space: `#a3a3a3` (neutral). Tracks: `#ff8552`, `#8aa8c8`, `#65d895`, `#b59cd6`, `#ffd166`, `#ef6f6f`, `#7eb4f0`, `#4caf82`, `#ffc857`, `#5dd6f0`, `#ce93d8`, `#ff7a7a`, `#90a4ae`, `#bcaaa4`. People: same palette in rotation.

### Typography
Inter (via `https://rsms.me/inter/inter.css`), with `font-feature-settings: 'cv11', 'ss01'`. Default `letter-spacing: -0.011em` on body, `-0.022em` on large titles, `-0.012em` on node titles.

### Sizing constants
- `FOCUS_PX = 260` (size of a scale=1 node)
- `PARENT_SCALE = 0.78`
- `RING1_SCALE = 0.46` (max — adaptive smaller, see Layout)
- `RING2_SCALE = 0.22`
- `VISIBLE_CAP = 12` (max ring-1 children visible at once)
- `R1 = min(310, dim*0.34)` where `dim = min(effW, h)`
- `R2 = min(560, dim*0.62)`
- Parent corner: `(38, cy - 60)` viewport-anchored

### Border radius
- Spheres: 50%
- Cards/inputs: 10px
- Pills/rails: 100px
- Small badges: 4px

## Sample Data
A 19-node graph. See `fisheye-data.js`:
- 1 workspace (Acme Inc.)
- 2 spaces (CRM, Personal)
- 14 tracks under CRM (chosen so the 12-cap kicks in and infinite-scroll is demoable)
- 3 people (Eldon, Jordan, Pat)

Replace with real entities from your data layer. Helpers `neighborsOf(id)` and `chainTo(id)` are pure and small — port them directly.

## Files in this bundle
- `Fish-eye Graph.html` — shell with all CSS and `<script>` tags. **Start here.**
- `fisheye-data.js` — vanilla JS, `NODES`/`EDGES`/`PARENT`/`MOCK_ENTRIES`/`PRESENCE` + helpers (`neighborsOf`, `chainTo`, `hexToRgba`, `entriesFor`, `presenceFor`).
- `fisheye-layout.js` — vanilla JS, the pure `computeLayout()` function + animation primitives (`easeInOutCubic`, `lerp`, `lerpNode`, `reconcileLayouts`, `focusFromLayout`).
- `fisheye-panel.jsx` — Babel-transpiled JSX, the side `Panel` component + sub-components (`Avatar`, `EntryCard`, `CommentBubble`, `PresenceRow`).
- `fisheye-app.jsx` — Babel-transpiled JSX, the `App` component (state, drill, drag/wheel, render) + ReactDOM mount.

## Implementation Notes for the Developer

1. **Don't port the file split.** The split exists because the prototype is loaded via `<script>` tags. In a real codebase, organize by concern: a `useFisheyeGraph` hook for state, a `useFisheyeMotion` hook for the transition runner, a `Panel` component, a `Stage` component, etc.

2. **The transition runner (`runTransition` in `fisheye-app.jsx`) is the heart of the design.** It's a manual `requestAnimationFrame` loop, not CSS transitions. CSS transitions can't express the "reconcile + lerp + orbital spin overlay" composition. If using Framer Motion, you'll need an imperative timeline (`useAnimate` / `useMotionValue`) or a custom rAF — animating layout positions per-node via `<motion.div animate={...}>` will fall apart on the spin overlay.

3. **The layout is a pure function of state.** Keep it that way in the port. The same function runs for static layout (after a transition lands), drag updates (during pointer-move), and as the *target* of every animated transition. Avoid computing positions inline in the render — pre-compute once per state change.

4. **Pointer events on nodes must `stopPropagation()` on `pointerdown`.** Otherwise the stage starts a rotation drag. The prototype does `onPointerDown={(e) => e.stopPropagation()}` on every interactive element.

5. **Wheel listener must be native + non-passive.** React's synthetic `onWheel` is passive and can't `preventDefault`. Attach via `useEffect` + `addEventListener('wheel', handler, { passive: false })`.

6. **Backdrop-filter blur is expensive.** The spheres and panel both use it. On lower-end devices consider falling back to a solid panel background and reducing sphere count.

7. **Use existing icon, avatar, and form primitives** from the host design system — the prototype uses inline SVGs and gradient-circle avatars as placeholders.

## Recommended Stack (if greenfield)
- React 18 + TypeScript
- Framer Motion for the panel slide; manual rAF for the universe lerp (Framer's layout animations don't compose with the spin overlay)
- Tailwind + CSS variables for tokens (matches the prototype's CSS variable approach)
- Tanstack Query (or your data fetcher of choice) for `entriesFor(focusId)` calls
- A WebSocket or supabase realtime subscription for `PRESENCE` and live comments
