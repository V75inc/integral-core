import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { UserPlus } from 'lucide-react';
import { Modal } from '../../components/ui/Modal';
import { Button } from '../../components/ui/Button';
import { Input, Text } from '../../ui';
import { appsApi } from '../../api/apps';
import { useToast } from '../../context/ToastContext';
import { errorMessageFromAxios } from '../../api/helpers';
import type { Entry } from '../../types';
import {
  checkCandidateHireReadiness,
  completeKanbanCandidateHire,
  confirmKanbanCandidateOffer,
  contractTemplateId,
  hireFlowErrorMessage,
  listEmploymentContractTemplates,
  fieldString,
  looksLikeEmail,
  suggestWorkEmail,
  type ContractTemplateOption,
} from './kanbanHireApi';
import type { KanbanHireIntent } from './kanbanHirePrompt';

interface KanbanHireModalProps {
  open: boolean;
  intent: KanbanHireIntent;
  candidate: Entry | null;
  workspaceId: string;
  /** When set, Complete hire is blocked (e.g. Personal workspace scope). */
  workspaceBlockReason?: string;
  recruitmentAppId?: string;
  onClose(): void;
  onCompleted?(): void;
}

export function KanbanHireModal({
  open,
  intent,
  candidate,
  workspaceId,
  workspaceBlockReason,
  recruitmentAppId,
  onClose,
  onCompleted,
}: KanbanHireModalProps) {
  const { showToast } = useToast();
  const [workEmail, setWorkEmail] = useState('');
  const [templateId, setTemplateId] = useState('');
  const [templates, setTemplates] = useState<ContractTemplateOption[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templatesError, setTemplatesError] = useState('');
  const [readiness, setReadiness] = useState<{
    ok: boolean;
    missing_fields?: { label?: string; key?: string }[];
  } | null>(null);
  const [readinessError, setReadinessError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const initCandidateRef = useRef<string | null>(null);

  const displayName = (candidate?.title || '').trim();
  const personalEmail = candidate ? fieldString(candidate, 'email') : '';

  useEffect(() => {
    if (!open || !candidate) {
      initCandidateRef.current = null;
      return;
    }
    if (initCandidateRef.current === candidate.id) return;
    initCandidateRef.current = candidate.id;
    setTemplateId(contractTemplateId(candidate));
    setReadiness(null);
    setReadinessError('');
  }, [open, candidate]);

  useEffect(() => {
    if (!open || !workspaceId) return;
    let cancelled = false;
    setTemplatesLoading(true);
    setTemplatesError('');
    void listEmploymentContractTemplates(workspaceId)
      .then(rows => {
        if (cancelled) return;
        setTemplates(rows);
        if (!rows.length) {
          setTemplatesError(
            'No published employment contract templates found. Open Document Templates, publish a version, then try again.',
          );
        }
      })
      .catch(err => {
        if (cancelled) return;
        setTemplates([]);
        setTemplatesError(errorMessageFromAxios(err, 'Could not load contract templates'));
      })
      .finally(() => {
        if (!cancelled) setTemplatesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, workspaceId]);

  useEffect(() => {
    if (!open || !candidate || !templates.length) return;
    const current = templateId.trim();
    const stillValid = current && templates.some(t => t.id === current);
    if (stillValid) return;
    const preferred =
      templates.find(t => t.id === contractTemplateId(candidate))?.id ||
      templates[0]?.id;
    if (preferred) setTemplateId(preferred);
  }, [open, candidate, templateId, templates]);

  useEffect(() => {
    if (!open || !candidate) return;
    let cancelled = false;
    (async () => {
      try {
        const settingsResp = recruitmentAppId
          ? await appsApi.getAppSettings(recruitmentAppId)
          : null;
        const domain = String(settingsResp?.settings?.company_email_domain || '');
        if (!cancelled) {
          setWorkEmail(suggestWorkEmail(displayName, domain));
        }
      } catch {
        if (!cancelled) setWorkEmail('');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, candidate, recruitmentAppId, displayName]);

  const refreshReadiness = useCallback(async () => {
    if (!candidate) return;
    if (!templateId) {
      setReadiness(null);
      setReadinessError('Choose an employment contract template below.');
      return;
    }
    setReadinessError('');
    try {
      const report = await checkCandidateHireReadiness(candidate, templateId);
      if (report.error) {
        setReadiness({ ok: false, missing_fields: report.missing_fields });
        setReadinessError(report.error);
        return;
      }
      setReadiness(report);
      if (!report.ok) setReadinessError('');
    } catch (err) {
      setReadiness(null);
      setReadinessError(errorMessageFromAxios(err, 'Readiness check failed'));
    }
  }, [candidate, templateId]);

  useEffect(() => {
    if (open && candidate && templateId) void refreshReadiness();
  }, [open, candidate, templateId, refreshReadiness]);

  const isOffer = intent === 'offer';

  const blockReason = useMemo(() => {
    if (submitting) return null;
    if (!candidate) return 'No candidate selected.';
    if (workspaceBlockReason) return workspaceBlockReason;
    if (!workspaceId) return 'Workspace context is missing.';
    if (templatesLoading) return 'Loading contract templates…';
    if (!templateId) return 'Choose an employment contract template.';
    if (!isOffer) {
      if (!personalEmail) {
        return 'Add a personal email on the candidate record (Edit on the card) — login credentials are sent there.';
      }
      if (!looksLikeEmail(personalEmail)) {
        return 'Candidate personal email is not valid. Edit the candidate and use a full address (e.g. name@example.com).';
      }
      if (!workEmail.trim()) return 'Enter a company email address.';
      if (!looksLikeEmail(workEmail)) {
        return 'Company email must be a valid address (e.g. name@yourcompany.com).';
      }
    }
    if (readinessError) return readinessError;
    if (!readiness) return 'Checking template fields…';
    if (!readiness.ok) return 'Complete the missing candidate fields listed above.';
    return null;
  }, [
    candidate,
    workspaceId,
    workspaceBlockReason,
    templateId,
    workEmail,
    personalEmail,
    readiness,
    readinessError,
    submitting,
    templatesLoading,
    isOffer,
  ]);

  const handleSubmit = async () => {
    if (!candidate || blockReason) return;
    setSubmitting(true);
    try {
      if (isOffer) {
        await confirmKanbanCandidateOffer(candidate, templateId);
        showToast('Candidate moved to Offer.', 'success');
      } else {
        const result = await completeKanbanCandidateHire({
          candidate,
          workspaceId,
          workEmail: workEmail.trim(),
          contractTemplateId: templateId,
        });
        const parts = [`Employee ${result.employeeId} created.`];
        if (result.credentialsEmailed) parts.push('Credentials emailed.');
        if (result.formEmailed) parts.push('Onboarding form emailed.');
        else if (result.formUrl) parts.push('Onboarding link minted.');
        showToast(parts.join(' '), 'success');
      }
      onCompleted?.();
      onClose();
    } catch (err) {
      showToast(
        hireFlowErrorMessage(err, isOffer ? 'Could not confirm offer' : 'Hire failed'),
        'error',
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (!candidate) return null;

  const missing = readiness?.missing_fields || [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={isOffer ? 'Confirm offer' : 'Complete hire'}
    >
      <Modal.Body>
        <div className="flex items-start gap-3">
          <UserPlus className="mt-0.5 shrink-0" size={20} />
          <div className="min-w-0">
            <Text as="p" variant="body" className="font-medium">
              {candidate.title || 'Candidate'}
            </Text>
            <Text as="p" variant="body-sm" tone="subtle">
              {isOffer
                ? 'Choose the employment contract template and ensure offer fields on the candidate are complete before moving to Offer.'
                : 'Choose the employment contract template, confirm work email, then provision HRM records and onboarding.'}
            </Text>
          </div>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Employment contract template</span>
          <select
            className="app-input w-full"
            value={templateId}
            disabled={templatesLoading || submitting || !templates.length}
            onChange={e => setTemplateId(e.target.value)}
          >
            {!templates.length ? (
              <option value="">No templates available</option>
            ) : (
              templates.map(t => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))
            )}
          </select>
          {templatesError ? (
            <Text as="p" variant="meta" tone="danger">
              {templatesError}
            </Text>
          ) : null}
        </label>

        {readinessError ? (
          <Text as="p" variant="body-sm" tone="danger">
            {readinessError}
          </Text>
        ) : readiness?.ok ? (
          <Text as="p" variant="body-sm" tone="success">
            Contract template fields are complete on this candidate.
          </Text>
        ) : readiness && !readiness.ok ? (
          <div>
            <Text as="p" variant="body-sm" tone="danger">
              Complete these fields on the candidate (Edit on the card) before continuing:
            </Text>
            <ul className="mt-1 list-disc pl-5">
              {missing.slice(0, 8).map(m => (
                <Text as="li" key={m.key || m.label} variant="body" tone="subtle">{m.label || m.key}</Text>
              ))}
            </ul>
          </div>
        ) : null}

        {!isOffer ? (
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium">Work email</span>
            <Input
              type="email"
              value={workEmail}
              onChange={e => setWorkEmail(e.target.value)}
              autoComplete="off"
            />
          </label>
        ) : null}
      </Modal.Body>
      <Modal.Footer>
        <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center sm:justify-end">
          {blockReason && !submitting ? (
            <Text as="p" variant="meta" tone="subtle" className="sm:mr-auto sm:text-left text-right">
              {blockReason}
            </Text>
          ) : (
            <span className="hidden sm:block sm:flex-1" aria-hidden />
          )}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose} disabled={submitting}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => void refreshReadiness()}
              disabled={submitting}
            >
              Recheck
            </Button>
            <Button
              type="button"
              variant="primary"
              disabled={Boolean(blockReason)}
              onClick={() => void handleSubmit()}
            >
              {submitting ? 'Working…' : isOffer ? 'Move to Offer' : 'Complete hire'}
            </Button>
          </div>
        </div>
      </Modal.Footer>
    </Modal>
  );
}
