import React, { useState, useCallback, useMemo } from "react";
import {
  ChevronRight,
  ChevronDown,
  Copy,
  Check,
  Search
} from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────


interface JsonViewerProps {
  data: unknown;
  /** Expand nodes up to this depth on first render. Default 1. */
  defaultExpandDepth?: number;
  /** Max height of the scroll area (CSS value). Default '60vh'. */
  maxHeight?: string;
  /** Dark mode palette. Default false. */
  dark?: boolean;
}

// ── Palette ────────────────────────────────────────────────────────────────

interface Palette {
  key: string;
  string: string;
  number: string;
  boolean: string;
  null: string;
  bracket: string;
  muted: string;
  bg: string;
  hoverBg: string;
  badge: string;
  badgeText: string;
}

const LIGHT: Palette = {
  key: "#0550ae",
  string: "#0a7c42",
  number: "#b35900",
  boolean: "#9333ea",
  null: "#6b7280",
  bracket: "#475569",
  muted: "#94a3b8",
  bg: "#ffffff",
  hoverBg: "#f1f5f9",
  badge: "#e2e8f0",
  badgeText: "#475569"
};

const DARK: Palette = {
  key: "#79c0ff",
  string: "#7ee787",
  number: "#ffa657",
  boolean: "#d2a8ff",
  null: "#8b949e",
  bracket: "#8b949e",
  muted: "#6e7681",
  bg: "#0d1117",
  hoverBg: "#161b22",
  badge: "#21262d",
  badgeText: "#8b949e"
};

// ── Helpers ────────────────────────────────────────────────────────────────

function typeOf(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

function isExpandable(value: unknown): boolean {
  return (
    value !== null &&
    typeof value === "object" &&
    Object.keys(value as object).length > 0
  );
}

function countItems(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  if (value !== null && typeof value === "object") {
    return Object.keys(value as object).length;
  }
  return 0;
}

function previewValue(value: unknown, palette: Palette): React.ReactNode {
  const t = typeOf(value);
  switch (t) {
    case "string":
      // Preserve embedded newlines + wrap long strings (e.g. a multi-line
      // system_prompt) instead of collapsing them into one run-on line.
      return (
        <span
          style={{
            color: palette.string,
            whiteSpace: "pre-wrap",
            overflowWrap: "anywhere",
            minWidth: 0
          }}
        >
          "{String(value)}"
        </span>
      );
    case "number":
      return <span style={{ color: palette.number }}>{String(value)}</span>;
    case "boolean":
      return <span style={{ color: palette.boolean }}>{String(value)}</span>;
    case "null":
      return <span style={{ color: palette.null }}>null</span>;
    default:
      return null;
  }
}

// ── Row ────────────────────────────────────────────────────────────────────

interface JsonNodeProps {
  nodeKey: string | null;
  value: unknown;
  depth: number;
  defaultExpandDepth: number;
  palette: Palette;
  searchTerm: string;
  isLast: boolean;
}

const JsonNode: React.FC<JsonNodeProps> = ({
  nodeKey,
  value,
  depth,
  defaultExpandDepth,
  palette,
  searchTerm,
  isLast
}) => {
  const [expanded, setExpanded] = useState(depth < defaultExpandDepth);
  const [copied, setCopied] = useState(false);

  const expandable = isExpandable(value);
  const itemCount = countItems(value);
  const type = typeOf(value);

  const handleCopy = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    },
    [value],
  );

  const toggle = useCallback(() => {
    if (expandable) setExpanded((p) => !p);
  }, [expandable]);

  // Search match highlight
  const matchesSearch =
    searchTerm.length > 0 &&
    ((nodeKey?.toLowerCase().includes(searchTerm.toLowerCase()) ?? false) ||
      (type !== "object" &&
        type !== "array" &&
        String(value).toLowerCase().includes(searchTerm.toLowerCase())));

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "flex-start",
    gap: 4,
    padding: "1px 0",
    borderRadius: 4,
    backgroundColor: matchesSearch ? palette.hoverBg : "transparent",
    // Allow long / multi-line leaf values to wrap within the row instead of
    // forcing a single overflowing line.
    flexWrap: "wrap",
    minWidth: 0
  };

  const indent = depth * 14;

  if (!expandable) {
    return (
      <div style={{ ...rowStyle, paddingLeft: indent }} className="json-row">
        <span style={{ width: 16, flexShrink: 0 }} />
        {nodeKey !== null && (
          <span style={{ color: palette.key, flexShrink: 0 }}>
            "{nodeKey}":{" "}
          </span>
        )}
        {previewValue(value, palette)}
        {!isLast && <span style={{ color: palette.bracket }}>,</span>}
        <button
          onClick={handleCopy}
          className="json-copy-btn"
          style={{
            marginLeft: 6,
            opacity: 0,
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 0,
            color: palette.muted,
            display: "inline-flex",
            alignItems: "center"
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
    );
  }

  const entries: [string | null, unknown][] = Array.isArray(value)
    ? value.map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);

  const openBracket = Array.isArray(value) ? "[" : "{";
  const closeBracket = Array.isArray(value) ? "]" : "}";

  return (
    <div>
      <div
        style={{ ...rowStyle, paddingLeft: indent, cursor: "pointer" }}
        className="json-row"
        onClick={toggle}
      >
        <span
          style={{
            width: 16,
            flexShrink: 0,
            display: "inline-flex",
            alignItems: "center",
            color: palette.muted
          }}
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
        {nodeKey !== null && (
          <span style={{ color: palette.key, flexShrink: 0 }}>
            "{nodeKey}":{" "}
          </span>
        )}
        <span style={{ color: palette.bracket }}>
          {openBracket}
          {!expanded && (
            <>
              <span style={{ color: palette.muted, margin: "0 4px" }}>
                {itemCount} {itemCount === 1 ? "item" : "items"}
              </span>
              {closeBracket}
              {!isLast && <span style={{ color: palette.bracket }}>,</span>}
            </>
          )}
        </span>
        <button
          onClick={handleCopy}
          className="json-copy-btn"
          style={{
            marginLeft: 6,
            opacity: 0,
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 0,
            color: palette.muted,
            display: "inline-flex",
            alignItems: "center"
          }}
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
      {expanded && (
        <div>
          {entries.map(([k, v], i) => (
            <JsonNode
              key={k ?? i}
              nodeKey={Array.isArray(value) ? null : k}
              value={v}
              depth={depth + 1}
              defaultExpandDepth={defaultExpandDepth}
              palette={palette}
              searchTerm={searchTerm}
              isLast={i === entries.length - 1}
            />
          ))}
          <div style={{ paddingLeft: indent + 16 }}>
            <span style={{ color: palette.bracket }}>
              {closeBracket}
              {!isLast && <span>,</span>}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Main ───────────────────────────────────────────────────────────────────

const JsonViewer: React.FC<JsonViewerProps> = ({
  data,
  defaultExpandDepth = 1,
  maxHeight = "60vh",
  dark = false
}) => {
  const [searchTerm, setSearchTerm] = useState("");
  const [copiedAll, setCopiedAll] = useState(false);
  const palette = dark ? DARK : LIGHT;

  const handleCopyAll = useCallback(() => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 1500);
  }, [data]);

  const itemCount = useMemo(() => countItems(data), [data]);

  return (
    <div
      style={{
        fontFamily:
          "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        fontSize: 12.5,
        lineHeight: 1.6,
        color: palette.bracket,
        backgroundColor: palette.bg
      }}
    >
      {/* Toolbar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "8px 10px",
          borderBottom: `1px solid ${dark ? "#21262d" : "#e2e8f0"}`,
          position: "sticky",
          top: 0,
          backgroundColor: palette.bg,
          zIndex: 1
        }}
      >
        <div style={{ position: "relative", flex: 1 }}>
          <Search
            size={13}
            style={{
              position: "absolute",
              left: 8,
              top: "50%",
              transform: "translateY(-50%)",
              color: palette.muted,
              pointerEvents: "none"
            }}
          />
          <input
            type="text"
            placeholder="Filter keys & values…"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            style={{
              width: "100%",
              padding: "5px 8px 5px 26px",
              fontSize: 12,
              fontFamily: "inherit",
              border: `1px solid ${dark ? "#30363d" : "#cbd5e1"}`,
              borderRadius: 6,
              backgroundColor: dark ? "#0d1117" : "#ffffff",
              color: dark ? "#c9d1d9" : "#1e293b",
              outline: "none"
            }}
          />
        </div>
        <span
          style={{
            fontSize: 11,
            color: palette.muted,
            whiteSpace: "nowrap"
          }}
        >
          {itemCount} {itemCount === 1 ? "key" : "keys"}
        </span>
        <button
          onClick={handleCopyAll}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            padding: "5px 10px",
            fontSize: 11,
            fontWeight: 500,
            border: `1px solid ${dark ? "#30363d" : "#cbd5e1"}`,
            borderRadius: 6,
            backgroundColor: dark ? "#21262d" : "#f8fafc",
            color: dark ? "#c9d1d9" : "#475569",
            cursor: "pointer",
            whiteSpace: "nowrap"
          }}
        >
          {copiedAll ? <Check size={13} /> : <Copy size={13} />}
          {copiedAll ? "Copied" : "Copy all"}
        </button>
      </div>

      {/* Tree */}
      <div
        style={{
          padding: "10px 12px",
          overflow: "auto",
          maxHeight
        }}
      >
        {data === undefined || data === null ? (
          <div
            style={{
              color: palette.muted,
              fontStyle: "italic",
              padding: "8px 0"
            }}
          >
            No data
          </div>
        ) : (
          <JsonNode
            nodeKey={null}
            value={data}
            depth={0}
            defaultExpandDepth={defaultExpandDepth}
            palette={palette}
            searchTerm={searchTerm}
            isLast={true}
          />
        )}
      </div>

      {/* Footer hint */}
      <div
        style={{
          padding: "6px 12px",
          borderTop: `1px solid ${dark ? "#21262d" : "#e2e8f0"}`,
          fontSize: 10.5,
          color: palette.muted
        }}
      >
        Tip: hover a row to copy that node · click brackets to collapse
      </div>

      <style>{`
        .json-row:hover { background-color: ${palette.hoverBg} !important; }
        .json-row:hover .json-copy-btn { opacity: 1 !important; }
        .json-copy-btn:hover { color: ${palette.key} !important; }
      `}</style>
    </div>
  );
};

export default JsonViewer;
