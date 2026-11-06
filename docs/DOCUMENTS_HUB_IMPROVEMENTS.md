# Documents hub: where it falls short and what to build

Status: proposal. Based on reading the Documents (1.13.17) and Document Templates (1.2.13) bundles, the editor source (`frontend/src/features/documents/editor`) and the other apps' manifests and tools. The editor was read, not run in a browser; "missing" means no extension, toolbar entry or tool was found.

## What exists

| Area | Today |
|---|---|
| Documents app | Library of filed originals, a knowledge base built from them or from Google Drive, and the door other apps call (`generate_document`, `file_generated_document`, `check_generated_filing`). Renders docx, pptx, pdf and markdown; previews pdf, xlsx, docx, pptx; Drive sync (pull, publish, edit in Google). |
| Document Templates app | Document types, layouts, templates, template versions with draft and publish; merge-field catalog; saved signature vault. |
| Editor | TipTap. Bold, italic, underline, strike, colour, highlight, links, alignment, indent, bullet and numbered lists, font family and size, tables (insert), page break, current-date token, merge-field tokens, signature placeholder, "my signature", repeat section, conditional section, block layout. |
| Types requested by apps | payslip (all country payrolls), invoice, quote, credit note, proposal, offer letter, job letter, employment contract, company letter. |

## Editor gaps

| Gap | Why it matters | Priority |
|---|---|---|
| Headings and named styles; automatic clause and section numbering; table of contents | contracts, policies, SOPs and handbooks cannot be authored properly without them | high |
| Page headers and footers, page numbers ("page X of Y"), watermark (DRAFT, CONFIDENTIAL) | every formal document needs them; letterhead is currently only a layout concern | high |
| Inline images and logos placed in the body; QR codes and barcodes | payslips, invoices, certificates, document verification | medium |
| Tables: merged cells, borders, repeating header rows, totals computed from fields | invoices, registers, schedules | medium |
| Comments, suggestions, an approval step; compare two template versions | review of contracts and policy changes | medium |
| Checkboxes and form fields; multi-column layout | forms, onboarding packs | low |
| Template preview with sample data; validation of unfilled merge fields before publish | publishing a template that renders blanks is only discovered at generation | high |

## Document type coverage

| Source app | Types with no template today | Priority |
|---|---|---|
| Payroll, Human Resources | warning letter, termination letter, leave approval letter, salary / bank / embassy letter | medium |
| Finance | customer statement, receipt, purchase order, remittance advice | medium |
| Sales, legal | NDA, statement of work, master services agreement | medium |
| QMS, Projects | SOP, audit report, corrective-action form, project status report | low |
| Payroll (all countries) | NIS, PAYE, 7B and annual forms are built in code (for example `_nis_form_pdf.py`), outside the template system | medium |

## Hub-level changes

1. **Lifecycle on filed documents:** draft, in review, approved, filed, superseded, with retention. A `status` field exists but no workflow sits behind it.
2. **Filing integrity:** filing must be idempotent on the source entry (`external_id` is `generated:<entry id>:<file>`), failures must be reported, and deleting a source must reconcile its document. Live evidence in workspace `...85b8ff`: 273 payslips all filed, 30 documents whose payslip is gone, 4 folder-and-file pairs filed twice (INTEGRAL-48, INTEGRAL-49).
3. **One template system:** move statutory forms and per-country payslip designs onto Document Templates so there is one editor and one layout engine.
4. **Document packs:** one action that produces several documents in order (offer letter, contract, NDA, onboarding form), files them together and routes them to E-Sign.

## Phases

1. Filing integrity (smallest, fixes data problems already visible) and template preview with unfilled-field validation.
2. Editor foundations: headings with numbering and table of contents, page headers and footers with page numbers and watermark, inline images.
3. New document types in the order of how many apps need them: HR and payroll letters, then finance, then legal, then QMS.
4. Lifecycle workflow and review (comments, approval, version compare), then packs, then statutory forms onto templates.

## Open questions

- Is a `.docx` round trip (import a Word contract, edit, export) in scope, or does the editor stay PDF-first?
- Should approvals use the platform's existing approvals, or a Documents-specific flow?
- Which statutory forms must stay pixel-exact government layouts (form fill) versus template-driven?
- Retention rules: per document type, per country, or workspace setting?

## How this was checked

Manifests of all 27 bundles, the Documents and Document Templates tools and views, the editor extensions and toolbar, and a join of Payroll payslips to filed Documents in the 9106 workspace. Not done: running the editor, generating each document type, or a user study of what people try to author.

## Commercial opportunities

What already exists to build on: E-Sign is packaged as a `commercial_app` but only provides a member signature vault and a contract-decision tool (no request, send, sign and certificate workflow); documents carry a `client_safe` flag; Documents has Drive sync, a content-hash stamp (`stamp_content_hash`) and AI skills (produce, revise, answer-from-documents, ingest); the billing module has plans and metered AI credits; Documents and Document Templates are `community_app`, so a paid tier is a packaging decision rather than a rewrite.

| Offer | What a customer pays for | Builds on | Priority |
|---|---|---|---|
| Real e-signature workflow | send a document to one or more signers in order, reminders, signing page, tamper-evident signed copy with an audit certificate (who, when, from where, content hash), status in the hub | E-Sign vault, `stamp_content_hash`, Documents filing | 1 |
| Tracked client share links | share a document with a client by link with expiry and optional password; see opened, viewed, downloaded; accept or decline a proposal or quote, and an accepted quote creates the invoice (Sales to Finance) | `client_safe`, Sales proposals, Finance quotes and invoices | 2 |
| AI drafting and review, metered by credits | draft from a short brief, suggest clauses, flag risky or missing clauses in an uploaded contract, summarise, ask questions across the library | produce-document, revise-document, answer-from-documents, ingest skills, billing AI credits | 3 |
| Contract and obligation tracker | renewal and expiry alerts, key dates and obligations extracted from filed contracts, owner reminders | knowledge base and ingest, email-log | 4 |
| Compliance and retention | audit log, legal hold, retention schedules, access reports, watermarking for confidential copies | hub lifecycle (see above), filing integrity | 5 |
| Branding and white label | workspace-branded share links and signing pages, custom domain, branded emails | layouts, Organization legal entity | 6 |
| Integrations | Microsoft 365 / SharePoint alongside Google Drive, DocuSign routing, send-from-document email, webhooks and API for generation | Drive sync, email-log, HR DocuSign routing | 7 |
| Packaging and metering | plan tiers by documents generated, signature envelopes, storage and AI credits, with a usage view | billing catalog and AI credit limits | with each feature |

Out of scope for now (per the product owner): sellable or billable template and compliance packs.

Caveats: legal validity of electronic signatures varies by jurisdiction (US ESIGN, EU eIDAS, local Caribbean law) and needs counsel before a signing certificate is marketed as legally binding; demand and pricing have not been tested with customers, so the order above is a technical and revenue-logic guess to validate.
