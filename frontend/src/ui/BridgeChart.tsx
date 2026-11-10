/**
 * BridgeChart — the reasons a figure moved, one bar each around a zero line: bars to the right added to it,
 * bars to the left took from it (for example what changed net pay since the last run). Layer: Primitive.
 */
import { shortNumber } from './LineChart';
import { useChartWidth } from './useChartWidth';

export interface BridgeStep { key: string; label: string; value: number }
export interface BridgeChartProps {
  steps: BridgeStep[];
  /** Describes the chart for assistive technology. */
  ariaLabel: string;
  /** How a bar's amount reads in its hover tip; default is the whole number. */
  formatValue?: (value: number) => string;
  /** Legend text. */
  upLabel?: string;
  downLabel?: string;
}

export function BridgeChart({ steps, ariaLabel, formatValue = v => Math.round(v).toLocaleString('en-US'), upLabel = 'Added', downLabel = 'Took away' }: BridgeChartProps) {
  const { ref, width: W } = useChartWidth(340, 620, 620);
  if (!steps.length) return null;
  const rowH = 28, L = W < 460 ? 150 : 196, R = 8, H = steps.length * rowH + 8;
  const big = Math.max(...steps.map(s => Math.abs(s.value)), 1);
  const mid = L + (W - L - R) / 2, half = (W - L - R) / 2 - 58;
  return (
    <div ref={ref} className="flex flex-col gap-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full text-[12px] text-[var(--text-muted)]" role="img" aria-label={ariaLabel}>
        <line x1={mid} x2={mid} y1={0} y2={H} stroke="var(--viz-grid)" strokeWidth="1" />
        {steps.map((s, i) => {
          const y0 = i * rowH + 6, bh = 16, w = Math.max(3, (Math.abs(s.value) / big) * half), up = s.value >= 0;
          return (
            <g key={s.key}>
              <text x={L - 10} y={y0 + bh / 2 + 4} textAnchor="end" fill="currentColor">{s.label}</text>
              <rect x={up ? mid : mid - w} y={y0} width={w} height={bh} rx={4} fill={up ? 'var(--viz-up)' : 'var(--viz-down)'}><title>{`${s.label}: ${up ? '+' : '−'}${formatValue(Math.abs(s.value))}`}</title></rect>
              <text x={up ? mid + w + 6 : mid - w - 6} y={y0 + bh / 2 + 4} textAnchor={up ? 'start' : 'end'} fill="var(--text)">{up ? '+' : '−'}{shortNumber(Math.abs(s.value))}</text>
            </g>
          );
        })}
      </svg>
      <div className="flex flex-wrap gap-4 text-xs text-[var(--text-muted)]">
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: 'var(--viz-up)' }} />{upLabel}</span>
        <span className="inline-flex items-center gap-1.5"><i className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: 'var(--viz-down)' }} />{downLabel}</span>
      </div>
    </div>
  );
}
