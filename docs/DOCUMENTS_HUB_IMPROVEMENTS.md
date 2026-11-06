# Documents hub: where it falls short and what to build

Status: proposal. Based on reading the Documents (1.13.17) and Document Templates (1.2.13) bundles, the editor source (`frontend/src/features/documents/editor`) and the other apps' manifests and tools. The editor was read, not run in a browser; "missing" means no extension, toolbar entry or tool was found.

## What exists

| Area | Today |
|---|---|
| Documents app | Library of filed originals, a knowledge base built from them or from Google Drive, and the door other apps call (`generate_document`, `file_generated_document`, `check_generated_filing`). Renders docx, pptx, pdf and markdown; previews pdf, xlsx, docx, pptx; Drive sync (pull, publish, edit in Google). |
| Document Templates app | Document types, layouts, templates, template versions with draft and publish; merge-field catalog; saved signature vault. |
| Editor | TipTap. Bold, italic, underline, strike, colour, highlight, links, alignment, indent, bullet and numbered lists, font family and size, tables (insert), page break, current-date token, merge-field tokens, signature placeholder, "my signature", repeat section, conditional section, block layout. |
| Types requested by apps | payslip (all country payrolls), invoice, quote, credit note, receipt, statement, proposal, offer letter, job letter, employment contract, company letter. |

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
| Finance | purchase order, remittance advice (invoice, quote, credit note, receipt and statement already exist: `finance/seeds/designs.py`) | medium |
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


Correction (2026-11-05): an earlier version listed Finance statements and receipts as missing. They exist (`finance/seeds/designs.py`, referenced by `document-templates/tools/letter_bodies.py` and the produce-document skill); only purchase order and remittance advice are missing.

## UI design (Documents app screens)

Reviewed in the running 9106 workspace (Documents app: app page, Documents track with its Folders and Documents views, and a filed document's page).

### What is wrong today

| Screen | Observation | Consequence |
|---|---|---|
| App page | The generic Core app page: name, description, "Start here: Open Documents", then a list of three tracks (Documents, Knowledge, Library). Nothing says what needs attention. | the "hub" has no front door |
| Folders view | A bare Name / Source / Modified / Synced table. One row ("Guyana Payroll", "42 folders, 40 files") above a large empty area. Two search boxes (track search and "Search files"). No file icons, no breadcrumb, no preview, no sorting or filtering. | cannot browse or find a payslip by person, month or app |
| Documents view | Generic table: Title, Kind, File type, Status, Client-safe, Stale. Kind is "Document" and Status is "Final" on every row. Titles are file names (`qa-one.pdf`). Folder (App / Year / Month / Person), source record, date and size are not shown. | rows are indistinguishable; the context that makes a document useful is hidden |
| Document page | Opens on an empty editor ("Start writing your document...") for a generated PDF; the actual file is under the "File (1)" tab. The details column is about 100 px wide so help text wraps one word per line. Labels are internal terms (Kind, Edited in, Stale, Sources, "text means the file is built from this text"). The white editor page sits on a dark UI with only a basic toolbar. | the most common task, looking at the file, takes a hidden tab; the page reads like a database form |

### Principles

1. Open on the thing the person came for: the file preview for a filed document, the content for a draft.
2. Show provenance everywhere: where a document came from (for example "Payslip: Contractor One, May 2027") links to the source record.
3. Status and next action are always visible: a status chip and the next step (approve, send for signature, share, download).
4. Use words people use, not field names: "Out of date: its source changed" instead of "Stale", "Master copy: file or text" instead of "Edited in".
5. A hub has a front door: what needs attention first, then recent, then browse.

### Wireframes

Hub home

```
Documents                                  [ Search everything...        ] [ + New document v ]
-------------------------------------------------------------------------------------------------
Needs attention                                          Recent
 [!] 3 drafts waiting for review                          payslip-contractor-one-may-2027.pdf
 [!] 2 out of date (source changed)                       offer-letter-j-singh.pdf
 [!] 1 awaiting signature  (2 days)                       invoice-1042.pdf  ...
 [!] 30 orphaned (source deleted)  -> review
-------------------------------------------------------------------------------------------------
Browse by source                     Payroll 273   HR 12   Finance 9   Sales 4   Uploaded 5
```

Library (folders and files)

```
Documents / Guyana Payroll / 2027 / May                            [list | grid]  [Sort: Modified v]
Filters: [Source v] [Type v] [Status v] [Client-safe] [Out of date]                 [ Search here ]
-------------------------------------------------------------------------------------------------
 [pdf] Contractor One - May 2027 payslip   Final   Payroll > Payslip   12 May   42 KB   [...]
 [pdf] WK Fifty - May 2027 payslip         Final   Payroll > Payslip   12 May   41 KB   [...]
                                                      | Preview pane (page 1 of the selected file) |
```

Document page

```
<- Documents / Guyana Payroll / 2027 / May
Contractor One - May 2027 payslip   [Final]   From: Payslip (Contractor One, May 2027)  ->
[ Preview ] [ Edit text ] [ Details ] [ History ] [ Sources ]       [Send for signature] [Share] [Download]
-------------------------------------------------------------------------------------------------
 +------ file preview ------+   Details
 |                          |   Status        Final
 |                          |   Type          Payslip
 |                          |   Visible to    Staff only   [Make client-safe]
 +--------------------------+   Filed         12 May 2027 by David Henry
```

Editor (templates and drafts)

```
[ Styles v ] [ B I U ] [ lists ] [ table ] [ image ] [ field ] [ page break ] [ TOC ]  Page 2 of 5  Saved
 Outline        |   header region (logo, company, page x of y)             |  Comments
  1 Parties     |   +------------------- page -------------------+         |   "Check clause 4"  (A. Khan)
  2 Services    |   |  1. Parties  ...                           |         |
  3 Fees        |   +--------------------------------------------+         |
                |   footer region (confidential, page numbers)             |
```

## Agent skills

Current skills: Documents (11) answer-from-documents, check-drive-updates, edit-in-google-editor, finish-file-filing, ingest-drive-folder, ingest-uploaded-file, produce-document, publish-to-drive, pull-from-drive, refresh-knowledge, revise-document; Document Templates (2) find-templates, manage-templates.

| Update | Why |
|---|---|
| produce-document: stop hard-coding the template key list; call `document_templates_list_active_templates` (or find-templates) and pick from the result, then fall back to the documented keys | the list in the skill drifts as templates are added (it already needs receipt and statement kept in sync); an agent that picks from the live list cannot ask for a template that does not exist |
| produce-document and revise-document: understand headings, numbering, headers, footers and page numbers once the editor supports them; state the format limits (what the PDF renderer drops) | agents should draft structured documents, not flat text |
| manage-templates: add preview with sample data and the unfilled-field check before publish | agents publish templates today without seeing them filled in |
| New skill: send-for-signature (needs the e-signature workflow) | route a finished document to signers and report status |
| New skill: share-document (tracked share link, accept or decline) | give clients a link and report whether it was opened |
| New skill: review-contract (AI clause review, metered by credits) | summarise and flag risky or missing clauses in an uploaded contract |
| New skill: build-document-pack | generate, file and route a set of documents in order |
| New skill: reconcile-filing | find orphaned and duplicate filed documents (30 and 4 in the test workspace today) and propose cleanup, never deleting without confirmation |
| All skills: describe where a document lives (App / Year / Month / Person folder convention) and how to link its source | answers and filings stay traceable |

Rule for this work: a skill is updated in the same change that delivers the feature it describes; a skill must not describe a capability that is not shipped yet.
