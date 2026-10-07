import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, FileText, Loader2 } from 'lucide-react';

import { publicSharingApi } from '../../api/sharing';
import { memberAssignedFormApi } from '../../features/memberAssignedForm/memberAssignedFormApi';
import { userSignaturesApi } from '../../api/userSignatures';
import { assertPdfBlob } from '../../lib/pdfjsLoader';
import { NativePdfBlobEmbed } from '../../components/entries/attachments/viewers/NativePdfBlobEmbed';
import { ViewerStatus } from '../../components/entries/attachments/viewers/ViewerStatus';
import { SignatureCanvas } from '../../components/signatures/SignatureCanvas';
import { Button } from '../../components/ui/Button';
import { Surface, Text, Textarea } from '../../ui';
import { LINE_ICON_STROKE } from '../../components/ui/IconWell';
import { useAuth } from '../../context/AuthContext';
import apiClient from '../../api/client';
import { errorMessageFromAxios } from '../../api/helpers';

interface ContractReviewStepProps {
  token: string;
  entryId: string;
  /** Use authenticated member assigned-form API instead of public share. */
  memberFormMode?: boolean;
  contractStatus?: string;
  /** Display label for the generated PDF (from field config or entry type). */
  documentTitle?: string;
  onAccepted: () => void;
  onRejected: () => void;
}

function ContractPdfViewer({
  blob,
  documentTitle,
}: {
  blob: Blob;
  documentTitle: string;
}) {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setExpanded(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded]);

  const downloadPdf = useCallback(() => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'document.pdf';
    anchor.rel = 'noopener';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }, [blob]);

  const linkClass =
    'text-[var(--accent)] underline cursor-pointer bg-transparent border-0 p-0 font-inherit text-inherit';

  return (
    <>
      <div className="space-y-2">
        <NativePdfBlobEmbed blob={blob} height={420} title={documentTitle} />
        <Text variant="body-sm" tone="muted" className="flex flex-wrap gap-x-3 gap-y-1">
          <button type="button" className={linkClass} onClick={() => setExpanded(true)}>
            Full screen preview
          </button>
          <button type="button" className={linkClass} onClick={downloadPdf}>
            Download PDF
          </button>
        </Text>
        <Text variant="meta" tone="muted" className="block">
          If the preview is empty in this browser, download the PDF — it uses the same file.
        </Text>
      </div>

      {expanded ? (
        <div
          className="fixed inset-0 z-[2000] flex items-center justify-center bg-black/60 p-4"
          role="dialog"
          aria-modal="true"
          aria-label={`${documentTitle} preview`}
          onClick={() => setExpanded(false)}
        >
          <Surface
            tone="panel" border="default" radius="card" elevation="pop"
            className="flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-[var(--panel-border)] px-4 py-2">
              <Text variant="body-sm" weight="semibold">
                {documentTitle}
              </Text>
              <Button type="button" variant="outline" size="sm" onClick={() => setExpanded(false)}>
                Close
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-auto p-3">
              <NativePdfBlobEmbed
                blob={blob}
                height="calc(92vh - 8rem)"
                title={documentTitle}
              />
            </div>
          </Surface>
        </div>
      ) : null}
    </>
  );
}

export function ContractReviewStep({
  token,
  entryId,
  memberFormMode = false,
  contractStatus,
  documentTitle = 'Document',
  onAccepted,
  onRejected,
}: ContractReviewStepProps) {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
  const [showSign, setShowSign] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [showReject, setShowReject] = useState(false);
  const [pdfBlob, setPdfBlob] = useState<Blob | null>(null);
  const [saveToProfile, setSaveToProfile] = useState(false);

  const status = String(contractStatus || 'pending').toLowerCase();
  const savedSigQ = useQuery({
    queryKey: ['me-signatures'],
    queryFn: () => userSignaturesApi.list(),
    enabled: Boolean(user),
  });
  const savedSig = savedSigQ.data?.[0];

  const loadContractPdf = useCallback(async (cacheBust?: string | number) => {
    const raw = memberFormMode
      ? await memberAssignedFormApi.fetchContractBlob(
          cacheBust ?? Date.now(),
          entryId,
        )
      : await publicSharingApi.fetchOnboardingContractBlob(
          token,
          entryId,
          cacheBust ?? Date.now(),
        );
    const blob = await assertPdfBlob(raw);
    setPdfBlob(blob);
  }, [entryId, token, memberFormMode]);

  const ensureGenerated = useCallback(async () => {
    setGenerating(true);
    setError(null);
    try {
      try {
        await loadContractPdf();
      } catch {
        if (memberFormMode) {
          await memberAssignedFormApi.generateContract({
            force: true,
            entryId,
          });
        } else {
          await publicSharingApi.generateOnboardingContract(token, entryId, {
            force: true,
          });
        }
        await loadContractPdf(Date.now());
      }
    } catch (err: unknown) {
      setError(errorMessageFromAxios(err, 'Could not load document'));
      setPdfBlob(null);
    } finally {
      setGenerating(false);
      setLoading(false);
    }
  }, [entryId, loadContractPdf, token, memberFormMode]);

  useEffect(() => {
    void ensureGenerated();
  }, [ensureGenerated]);

  const handleUseSavedSignature = async () => {
    if (!savedSig) return;
    try {
      const resp = await apiClient.get(`/me/signatures/${savedSig.id}/download`, {
        responseType: 'blob',
      });
      const reader = new FileReader();
      reader.onload = () => {
        setSignature(String(reader.result || ''));
        setShowSign(true);
      };
      reader.readAsDataURL(resp.data as Blob);
    } catch {
      setError('Could not load your saved signature');
    }
  };

  const handleAccept = async () => {
    if (!signature) {
      setError('Please draw your signature before accepting');
      return;
    }
    setDeciding(true);
    setError(null);
    try {
      if (saveToProfile && user) {
        await userSignaturesApi.save(signature);
      }
      if (memberFormMode) {
        await memberAssignedFormApi.decideContract(
          {
            action: 'accept',
            signature_png: signature,
          },
          entryId,
        );
      } else {
        await publicSharingApi.decideOnboardingContract(token, entryId, {
          action: 'accept',
          signature_png: signature,
        });
      }
      onAccepted();
    } catch (err: unknown) {
      setError(errorMessageFromAxios(err, 'Could not accept document'));
    } finally {
      setDeciding(false);
    }
  };

  const handleReject = async () => {
    setDeciding(true);
    setError(null);
    try {
      if (memberFormMode) {
        await memberAssignedFormApi.decideContract(
          {
            action: 'reject',
            reason: rejectReason.trim() || undefined,
          },
          entryId,
        );
      } else {
        await publicSharingApi.decideOnboardingContract(token, entryId, {
          action: 'reject',
          reason: rejectReason.trim() || undefined,
        });
      }
      onRejected();
    } catch (err: unknown) {
      setError(errorMessageFromAxios(err, 'Could not reject document'));
    } finally {
      setDeciding(false);
    }
  };

  if (status === 'accepted') {
    return (
      <div className="space-y-3">
        <Text variant="body" tone="muted">
          You accepted this document. Continue to the next steps.
        </Text>
        {!error && pdfBlob ? (
          <ContractPdfViewer blob={pdfBlob} documentTitle={documentTitle} />
        ) : null}
      </div>
    );
  }

  if (loading || generating) {
    return (
      <Text as="div" variant="body" tone="muted" className="flex items-center gap-2 py-8">
        <Loader2 size={18} className="animate-spin" strokeWidth={LINE_ICON_STROKE} />
        <Text variant="body" tone="muted">
          Preparing your document…
        </Text>
      </Text>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2">
        <FileText size={18} strokeWidth={LINE_ICON_STROKE} className="mt-0.5 shrink-0" />
        <div className="space-y-1">
          <Text variant="body" weight="semibold">
            Review and sign
          </Text>
          <Text variant="body-sm" tone="muted">
            Read the document below. Accept and sign to continue, or reject to stop.
          </Text>
        </div>
      </div>

      {error ? (
        <div className="space-y-3 rounded-[var(--radius-card)] border border-red-500/30 bg-red-500/5 p-3">
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-500" />
            <Text variant="body-sm" className="text-red-600">
              {error}
            </Text>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => void ensureGenerated()}>
            Try again
          </Button>
        </div>
      ) : pdfBlob ? (
        <ContractPdfViewer blob={pdfBlob} documentTitle={documentTitle} />
      ) : (
        <ViewerStatus state="error" message="Document PDF is not available yet." />
      )}

      {showReject ? (
        <div className="space-y-3 rounded-[var(--radius-card)] border border-[var(--panel-border)] p-3">
          <Text variant="body-sm" tone="muted">
            Optionally share why you are declining (this stops the form flow).
          </Text>
          <Textarea
            rows={3}
            value={rejectReason}
            onChange={e => setRejectReason(e.target.value)}
            placeholder="Reason for declining (optional)"
          />
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deciding}
              onClick={() => setShowReject(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={deciding}
              onClick={() => void handleReject()}
            >
              {deciding ? 'Submitting…' : 'Confirm reject'}
            </Button>
          </div>
        </div>
      ) : showSign ? (
        <div className="space-y-3 rounded-[var(--radius-card)] border border-[var(--panel-border)] p-3">
          <SignatureCanvas onChange={setSignature} />
          {user ? (
            <Text as="label" variant="body" tone="muted" className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={saveToProfile}
                onChange={e => setSaveToProfile(e.target.checked)}
              />
              Save this signature to my profile
            </Text>
          ) : null}
          {user ? (
            <Text variant="meta" tone="muted">
              To update or remove a saved signature later, open{' '}
              <strong>Apps → E-Sign → My signatures</strong> in your workspace.
            </Text>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deciding}
              onClick={() => {
                setShowSign(false);
                setSignature(null);
              }}
            >
              Back
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={deciding}
              onClick={() => void handleAccept()}
            >
              {deciding ? 'Signing…' : 'Sign and accept'}
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {user && savedSig ? (
            <Button type="button" variant="primary" onClick={() => void handleUseSavedSignature()}>
              Use saved signature
            </Button>
          ) : null}
          <Button type="button" variant="primary" onClick={() => setShowSign(true)}>
            {user && savedSig ? 'Draw new signature' : 'Accept'}
          </Button>
          <Button type="button" variant="outline" onClick={() => setShowReject(true)}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}

export function ContractReviewRejectedTerminal() {
  return (
    <div className="text-center py-8 space-y-4 animate-fade-in">
      <AlertTriangle size={56} className="mx-auto text-amber-500" />
      <div>
        <Text variant="heading-md" weight="semibold" as="h2">
          Form closed
        </Text>
        <Text variant="body-sm" tone="muted" as="p" className="block mt-1">
          You declined the document. Contact your workspace administrator if you need help.
        </Text>
      </div>
    </div>
  );
}
