import { useEffect, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';

import type { Attachment } from '../../../../types';
import { useAttachmentBlob } from './useAttachmentBlob';
import { ViewerStatus } from './ViewerStatus';
import { LINE_ICON_STROKE } from '../../../ui/IconWell';

/**
 * PDF.js-backed viewer.
 *
 * Uses the dynamic ``pdfjs-dist`` import so we don't bloat the
 * initial bundle for users who never open an attachment. The worker
 * is set from the CDN-hosted build that matches the installed
 * version — keeps the on-disk asset footprint small at the cost of
 * an extra network fetch on first open.
 *
 * ``source="preview"`` switches the data feed to the server-rendered
 * preview PDF (LibreOffice output cached at a sibling key). PPTX
 * uses that path; everything else uses ``"download"``.
 */

interface PdfViewerProps {
  attachment: Attachment;
  source?: 'download' | 'preview';
}

type PdfModule = typeof import('pdfjs-dist');
type PdfDocument = Awaited<ReturnType<PdfModule['getDocument']>['promise']>;

let pdfModulePromise: Promise<PdfModule> | null = null;

async function getPdfjs(): Promise<PdfModule> {
  if (!pdfModulePromise) {
    pdfModulePromise = (async () => {
      const mod = await import('pdfjs-dist');
      const workerVersion = mod.version;
      // The worker URL must match the API version exactly. Using the
      // cdnjs build of pdf.js because the legacy bundler variants of
      // pdfjs-dist still vary between minor releases.
      mod.GlobalWorkerOptions.workerSrc = `https://cdn.jsdelivr.net/npm/pdfjs-dist@${workerVersion}/build/pdf.worker.min.mjs`;
      return mod;
    })();
  }
  return pdfModulePromise;
}

export function PdfViewer({ attachment, source = 'download' }: PdfViewerProps) {
  const { blob, loading, error } = useAttachmentBlob(
    attachment.id,
    source === 'preview' ? 'preview' : 'download'
  );
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [doc, setDoc] = useState<PdfDocument | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [renderError, setRenderError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRenderError(null);
    setDoc(null);
    setPage(1);
    if (!blob) return;

    blob.arrayBuffer().then(async (buf) => {
      try {
        const pdfjs = await getPdfjs();
        const loadingTask = pdfjs.getDocument({
          data: new Uint8Array(buf),
        });
        const pdf = await loadingTask.promise;
        if (cancelled) {
          pdf.destroy().catch(() => undefined);
          return;
        }
        setDoc(pdf);
      } catch (e) {
        if (!cancelled) {
          setRenderError(
            e instanceof Error ? e.message : 'PDF failed to load'
          );
        }
      }
    });

    return () => {
      cancelled = true;
    };
  }, [blob]);

  useEffect(() => {
    if (!doc || !canvasRef.current) return;
    let cancelled = false;
    const canvas = canvasRef.current;
    (async () => {
      try {
        const p = await doc.getPage(page);
        if (cancelled) return;
        const viewport = p.getViewport({ scale: zoom * (window.devicePixelRatio || 1) });
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
            e instanceof Error ? e.message : 'Page failed to render'
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
  if (renderError) return <ViewerStatus state="error" message={renderError} />;
  if (!doc) return <ViewerStatus state="loading" />;

  const totalPages = doc.numPages;

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center justify-center gap-2 border-b border-[var(--panel-border)] bg-[var(--panel)] px-3 py-1.5">
        <button
          type="button"
          onClick={() => setPage((p) => Math.max(1, p - 1))}
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
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          disabled={page >= totalPages}
          aria-label="Next page"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-30"
        >
          <ChevronRight size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
        <span aria-hidden className="mx-1 h-3 w-px bg-[var(--panel-border)]" />
        <button
          type="button"
          onClick={() => setZoom((z) => Math.max(0.4, +(z - 0.2).toFixed(2)))}
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
          onClick={() => setZoom((z) => Math.min(3, +(z + 0.2).toFixed(2)))}
          aria-label="Zoom in"
          className="rounded-[var(--radius-input)] p-1 text-[var(--text-muted)] hover:text-[var(--text)]"
        >
          <ZoomIn size={14} strokeWidth={LINE_ICON_STROKE} />
        </button>
      </div>
      <div className="flex-1 overflow-auto bg-[var(--panel-2)]/40 p-4">
        <div className="mx-auto inline-block bg-white shadow-[var(--shadow-card)]">
          <canvas ref={canvasRef} />
        </div>
      </div>
    </div>
  );
}
