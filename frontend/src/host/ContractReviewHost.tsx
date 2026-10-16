import { Text } from '../ui';

/** Host extension slot for App-owned contract review workflows (Core ships a stub). */
export type ContractReviewHostProps = {
  token: string;
  entryId: string;
  memberFormMode?: boolean;
  contractStatus?: string;
  onAccepted: () => void;
  onRejected: () => void;
};

export function ContractReviewHost(_props: ContractReviewHostProps) {
  return (
    <Text variant="body" tone="muted">
      Document review for this step is supplied by your workspace app package.
    </Text>
  );
}
