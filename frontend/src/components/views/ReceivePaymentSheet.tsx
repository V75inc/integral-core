import { useEffect, useState } from 'react';
import { Modal } from '../ui/Modal';
import { Button } from '../ui/Button';
import { extensionsApi } from '../../api/extensions';
import type { Entry } from '../../types';

const PAYMENT_METHODS = [
  { value: 'cash', label: 'Cash' },
  { value: 'check', label: 'Cheque' },
  { value: 'transfer', label: 'Bank transfer' },
  { value: 'credit_card', label: 'Credit card' },
  { value: 'debit_card', label: 'Debit card' },
  { value: 'mobile_money', label: 'Mobile money' },
  { value: 'other', label: 'Other' },
] as const;

function relId(value: unknown): string {
  if (value && typeof value === 'object') {
    if (Array.isArray(value)) return relId(value[0]);
    const obj = value as Record<string, unknown>;
    return String(obj.id || obj.entry_id || '').trim();
  }
  return String(value || '').trim();
}

function todayIso(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function money(n: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: currency || 'USD',
    }).format(n);
  } catch {
    return n.toFixed(2);
  }
}

function operationErrorMessage(output: Record<string, unknown>): string | null {
  if (!output) return null;
  if (output.error || output.error_code) {
    return String(output.message || output.error_code || 'Payment failed');
  }
  return null;
}

export type ReceivePaymentSheetProps = {
  open: boolean;
  entry: Entry;
  appId: string;
  onClose: () => void;
  onSuccess: () => void;
};

export function ReceivePaymentSheet({
  open,
  entry,
  appId,
  onClose,
  onSuccess,
}: ReceivePaymentSheetProps) {
  const cf = (entry.custom_fields || {}) as Record<string, unknown>;
  const balance = Number(cf.balance);
  const currency = String(cf.currency || 'USD');
  const customerId = relId(cf.customer);
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState<string>('transfer');
  const [txnDate, setTxnDate] = useState(todayIso());
  const [reference, setReference] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setAmount(Number.isFinite(balance) && balance > 0 ? String(balance) : '');
    setMethod('transfer');
    setTxnDate(todayIso());
    setReference('');
    setError('');
    setSubmitting(false);
  }, [open, entry.id, balance]);

  const title = entry.title?.trim() || 'Invoice';
  const handleSubmit = async () => {
    setError('');
    if (!customerId) {
      setError('Invoice needs a customer before receiving payment.');
      return;
    }
    const amt = Number(amount);
    if (!Number.isFinite(amt) || amt <= 0) {
      setError('Enter how much you received (must be greater than zero).');
      return;
    }
    const bal = Number.isFinite(balance) ? balance : amt;
    if (amt > bal + 1e-9) {
      setError(
        `Amount ${money(amt, currency)} exceeds open balance ${money(bal, currency)}.`
      );
      return;
    }
    if (!txnDate) {
      setError('Enter the payment date.');
      return;
    }

    setSubmitting(true);
    try {
      const payload: Record<string, unknown> = {
        customer_id: customerId,
        total_amount: amt,
        direction: 'receive',
        payment_method: method,
        txn_date: txnDate,
        applications: [{ invoice_id: entry.id, applied_amount: amt }],
      };
      if (reference.trim()) payload.reference_number = reference.trim();

      const res = await extensionsApi.invokeOperation(
        appId,
        'payment_record',
        payload
      );
      const output = (res.output || {}) as Record<string, unknown>;
      const errMsg = operationErrorMessage(output);
      if (errMsg) {
        setError(errMsg);
        return;
      }
      onSuccess();
    } catch (e) {
      const msg =
        e && typeof e === 'object' && 'response' in e
          ? String(
              (e as { response?: { data?: { message?: string } } }).response
                ?.data?.message || ''
            )
          : '';
      setError(msg || (e instanceof Error ? e.message : 'Could not record payment'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Receive payment"
      variant="compact"
      width="max-w-dialog-confirm"
    >
      <Modal.Body>
        <p className="text-sm text-[var(--text-muted)] mb-3">
          {title}
          {Number.isFinite(balance) ? (
            <>
              {' '}
              · open balance {money(balance, currency)}
            </>
          ) : null}
        </p>
        <div className="grid gap-3">
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            Amount received
            <input
              type="number"
              min="0.01"
              step="any"
              value={amount}
              onChange={e => setAmount(e.target.value)}
              className="rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)]"
              autoFocus
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            Date
            <input
              type="date"
              value={txnDate}
              onChange={e => setTxnDate(e.target.value)}
              className="rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)]"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            Method
            <select
              value={method}
              onChange={e => setMethod(e.target.value)}
              className="rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)]"
            >
              {PAYMENT_METHODS.map(m => (
                <option key={m.value} value={m.value}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-muted)]">
            Reference #
            <input
              type="text"
              value={reference}
              onChange={e => setReference(e.target.value)}
              placeholder="Check # / txn ref"
              className="rounded-[var(--radius-input)] border border-[var(--panel-border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)]"
            />
          </label>
        </div>
        {error ? (
          <p className="mt-3 text-sm text-[var(--danger-fg)]">{error}</p>
        ) : (
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            Defaults to the open balance. Enter less for a partial payment.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={submitting}
            onClick={() => void handleSubmit()}
          >
            Record payment
          </Button>
        </div>
      </Modal.Body>
    </Modal>
  );
}
