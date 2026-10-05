import { useEffect, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';

import { getPdfjs } from '../../../../lib/pdfjsLoader';
import { NativePdfBlobEmbed } from './NativePdfBlobEmbed';
import { ViewerStatus } from './ViewerStatus';
import { LINE_ICON_STROKE } from '../../../ui/IconWell';

type PdfDocument = Awaited<
  ReturnType<Awaited<ReturnType<typeof getPdfjs>>['getDocument']>['promise']
>;

interface BlobPdfViewerProps {
  blob: Blob | null;
  loading?: boolean;
  error?: string | null;
  /** Max height for the scroll area (wizard embeds use a fixed height). */
  maxHeight?: number | string;
}

/**
 * PDF.js viewer for an in-memory blob (public share wizard, previews, etc.).
 * Avoids ``<iframe src="blob:…">`` — unreliable across browsers/CSP.
 */
export function BlobPdfViewer({
  blob,
  loading,
  error,
  maxHeight = 420,
}: BlobPdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<PdfDocument | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [useNativeEmbed, setUseNativeEmbed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setRenderError(null);
    setDoc(null);
    setPage(1);
    setUseNativeEmbed(false);
    if (!blob || loading || error) return;

    const fallbackTimer = window.setTimeout(() => {
      if (!cancelled) setUseNativeEmbed(true);
    }, 2500);

    void blob.arrayBuffer().then(async buf => {
      try {
        const pdfjs = await getPdfjs();
        const loadingTask = pdfjs.getDocument({ data: new Uint8Array(buf) });
        const pdf = await loadingTask.promise;
        if (cancelled) {
          pdf.destroy().catch(() => undefined);
          return;
        }
        window.clearTimeout(fallbackTimer);
        setDoc(pdf);
      } catch (e) {
        if (!cancelled) {
          window.clearTimeout(fallbackTimer);
          setUseNativeEmbed(true);
          setRenderError(
            e instanceof Error ? e.message : 'PDF failed to load',
          );
        }
      }
    }).catch(e => {
      if (!cancelled) {
        window.clearTimeout(fallbackTimer);
        setUseNativeEmbed(true);
        setRenderError(
          e instanceof Error ? e.message : 'PDF failed to load',
        );
      }
    });

    return () => {
      cancelled = true;
      window.clearTimeout(fallbackTimer);
    };
  }, [blob, error, loading]);

  useEffect(() => {
    if (!doc || !canvasRef.current) return;
    let cancelled = false;
    const canvas = canvasRef.current;
    (async () => {
      try {
        const p = await doc.getPage(page);
        if (cancelled) return;
        const viewport = p.getViewport({
          scale: zoom * (window.devicePixelRatio || 1),
        });
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = `${viewport.width / (window.devicePixelRatio || 1)}px`;
        canvas.style.height = `${viewport.height / (window.devicePixelRatio || 1)}px`;
        await p.render({ canvasContext: ctx, viewport }).promise;
      } catch (e) {
        if (!cancelled) {
          setRenderError(
            e instanceof Error ? e.message : 'Page failed to render',
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [doc, page, zoom]);

  if (loading) return <ViewerStatus state="loading" />;
  if (error) return <ViewerStatus state="error" message={error} />;
  if (blob && useNativeEmbed) {
    return <NativePdfBlobEmbed blob={blob} height={maxHeight} title="PDF preview" />;
  }
  if (renderError && blob) {
    return <NativePdfBlobEmbed blob={blob} height={maxHeight} title="PDF preview" />;
  }
  if (!blob || !doc) return <ViewerStatus state="loading" />;

  const totalPages = doc.numPages;

  return (
    <div className="overflow-hidden rounded-[var(--radius-card)] border border-[var(--panel-border)] bg-[var(--panel)]">
      <div className="flex flex-wrap items-center justify-center gap-2 border-b border-[var(--panel-border)] px-3 py-1.5">
        <button
          type="button"
          onClick={() => setPage(p => Math.max(1, p - 1))}
          disabled={page <= 1}
          aria-label="Previous page"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-30"
        >
          <ChevronLeft size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
        <span className="text-[11px] tabular-nums text-[var(--text-muted)]">
          {page} / {totalPages}
        </span>
        <button
          type="button"
          onClick={() => setPage(p => Math.min(totalPages, p + 1))}
          disabled={page >= totalPages}
          aria-label="Next page"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-30"
        >
          <ChevronRight size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
        <span aria-hidden className="mx-1 h-3 w-px bg-[var(--panel-border)]" />
        <button
          type="button"
          onClick={() => setZoom(z => Math.max(0.4, +(z - 0.2).toFixed(2)))}
          aria-label="Zoom out"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <ZoomOut size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
        <span className="text-[11px] tabular-nums text-[var(--text-muted)]">
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          onClick={() => setZoom(z => Math.min(3, +(z + 0.2).toFixed(2)))}
          aria-label="Zoom in"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <ZoomIn size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
      </div>
      <div
        className="overflow-auto bg-[var(--panel-2)]/40 p-4"
        style={{ maxHeight }}
      >
        <div className="mx-auto inline-block bg-white shadow-[var(--shadow-card)]">
          <canvas ref={canvasRef} />
        </div>
      </div>
    </div>
  );
}
