import {
  attachmentsApi,
  type AttachmentRecord,
} from '../../api/attachments';

export interface MemberOnboardingUploadOptions {
  fieldKey: string;
  entryId: string;
}

/**
 * Attachment upload path used by member-onboarding forms.
 * Delegates to the standard entry attachment endpoint; the field key is
 * retained for callers that tag the upload in form state.
 */
export const memberOnboardingApi = {
  async uploadAttachment(
    file: File,
    options: MemberOnboardingUploadOptions,
  ): Promise<AttachmentRecord & { fieldValue?: string }> {
    const record = await attachmentsApi.uploadForEntry(options.entryId, file);
    return {
      ...record,
      fieldValue: record.id,
    };
  },
};
