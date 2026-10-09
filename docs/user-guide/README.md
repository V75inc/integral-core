# Your guide to Integral

Want to run Integral on your own computer? Start with `integral up` from a launcher-enabled installation. It opens a private workspace in your browser and handles the local services for you. The [installation guide](../ops/LOCAL_INSTALLATION.md) covers obtaining the package, the source preview and everyday lifecycle commands.


Integral helps you bring information, collaboration, and AI assistance into one working environment. This guide begins with a small useful workflow, then explains how to grow it.

## 1. Find your bearings

Sign in to your installation, or create an account if registration is available. A new account receives a personal workspace. Email verification is surfaced as a reminder rather than blocking login; delivery depends on the installation's email configuration.

The workspace selector tells you where you are working. Your personal workspace is your own starting place. An organizational workspace brings a team together, with membership and resource permissions determining access.

An App groups related work. A Track collects records, and an Entry is one record. The App's Operational Model determines which record types, fields, and views are available. You may also encounter a standalone Track in a workspace.

Choose the workspace intentionally before creating or asking the AI to change information. Opening an accessible resource in another workspace may cause the interface to switch scope. Check the selected workspace when moving between teams.

If a resource is unavailable, you may have the wrong workspace, insufficient access, a revoked grant, or an old link. Ask the owner for access rather than assuming the item has been deleted.

## 2. A useful first session

Start with a simple collection you actually need: notes, requests, decisions, or another type your installation supports.

1. Select your workspace.
2. Create or open an App or Track using the available controls.
3. Add a few Entries. Give each one a meaningful title and complete required fields.
4. Open another available view of the same Track.
5. Open an Entry and inspect its fields, comments, relations, and attachments.
6. Ask the resident AI to summarize these records, then open the records it cites.

A second view is a second perspective on the same information. It is not a second copy to keep synchronized.

For a first AI request, be concrete:

> “In this workspace, summarize the requests in this Track. Link the records you use and identify anything you cannot determine.”

The model and tools available to your installation determine what can be completed. A useful answer should preserve uncertainty rather than supply missing facts by guesswork.

## 3. Workspaces and teams

Personal and organizational workspaces have different membership arrangements. Organization administrators manage the team and its workspace-level creation rights. Joining a workspace does not automatically make you the owner of every resource inside it.

An App, Track, or Entry can have direct collaborators. Access may also be inherited through a parent resource. A cross-workspace share can provide a limited guest path without making the recipient a general participant in unrelated work.

Keep a workspace for a coherent working boundary. Use resource sharing to narrow or extend access within that boundary. Avoid relying on a view filter as a privacy mechanism; a filter changes presentation, while resource permissions control access.

## 4. Apps and Tracks

An App gives a purpose a home. It can bring together Tracks, useful views, actions, skills, and summaries. Its Home presents package-defined information where configured; dashboards provide additional instance-level projections.

A Track is a collection of Entries. A record type supplies its fields and validation. Some Apps define several Track types; others provide one simple collection. Choose the type that represents the work rather than putting every kind of information into one general-purpose record.

If a library or package is available, inspect its description and requirements before installation. Installation may ask for settings before activation. Pausing an App changes its lifecycle and active capabilities; it should not be treated as deleting all its records. Owners and administrators should review the actual lifecycle controls before uninstalling or upgrading an App.

If you need a structure your installation does not provide, ask the resident to draft a design first. Explain the kinds of records, relationships, views, audience, and operations you need. Review the design before asking for implementation.

## 5. Entries: meaningful records

Use titles people will recognize. The title is useful in lists, relations, links, and AI responses. Complete required fields so the record fits its declared type.

Use the body for explanatory content and typed fields for values you want to validate, filter, relate, or present consistently. A date belongs in an appropriate date field if it should appear in a calendar. A person reference belongs in a member field. A file belongs in a file field or attachment surface.

When editing, check that the save completes. If another person or process changed the record, a revision conflict may require reloading and reconciling your changes. A stale tab is not authority to overwrite newer work.

Comments let collaborators discuss a record without replacing its content. Reactions and comments require the corresponding access. Editing your own comment and moderating another person's comment are different permissions.

Provenance identifies the source of a record where available: human, agent, connector, or system. Inspect supporting detail when it matters to your decision. Provenance describes origin; it does not guarantee that the content is correct.

## 6. Relations and deeper structure

A relation connects a record to another operational resource. Open its named destination to see the connected information you are allowed to access. An unavailable or restricted destination may still be referenced by a record you can read.

Some models use anchored Tracks. An anchor gives a record a related collection, such as supporting evidence or restricted detail. This relationship stays inside its workspace and follows the model's lifecycle and deletion rules.

Give independently managed information its own records and Tracks. A large JSON field holding dozens of operational items loses the separate identities, permissions, and navigation that make those items useful.

If a shared record would contain information for different audiences, separate the restricted portion into a properly governed resource. Integral does not offer general field-level privacy; hiding a field in a view does not make it secret.

## 7. Views and finding information

Use the views declared by your Track or App. Feeds suit reading and recent activity. Tables suit comparison. Boards suit grouping by a state or other supported field. Calendars suit dated work. Other registered or package-owned views can provide a more specific presentation.

A view may define its own filters, sorting, grouping, and record-type selection. If a record appears missing, clear the applicable filters and check your access. Changing a view does not change the security boundary.

Search and queries operate within authorized scope. Semantic search depends on the installation's retrieval backend. An App's declared query can provide a purpose-specific result rather than a general search.

Empty and unavailable are different states. If a widget reports an unavailable data source, do not interpret it as a count of zero. When a result matters, open the source records or inspect the relevant query result.

## 8. Files and attachments

Upload files through the provided attachment controls. Supported types and limits are set by the installation. Core's defaults allow up to 500 MiB per attachment, with separate batch limits; your host can choose lower values.

A file is linked to its record and read through an authorized preview or download path. Duplicate detection may reuse content rather than uploading another identical copy. Generated documents are distinct from arbitrary files pasted into a chat message.

The AI may receive extracted text or metadata from supported files. That is not the same as understanding every page or media format. Check important details against the original file.

Malware screening depends on the configured scanner. The default no-op scanner is not a screening service. Operators should state what their installation actually provides.

## 9. Sharing and permissions

Choose the smallest role that fits the collaboration.

| Resource role | Typical purpose |
|---|---|
| Viewer | Read accessible information |
| Commenter | Read and participate in comments; limited author-specific behavior depends on the action |
| Editor | Create and edit records |
| Admin | Edit records and curate resource configuration and sharing |
| Owner | Full resource authority, including owner-only deletion and transfer |

Workspace membership roles are separate from these resource roles. A workspace administrator is not automatically a direct owner of every child resource.

Inherited owner or admin access is capped at editor on child resources. To let someone curate a particular Track's schema or views, grant appropriate authority directly on that Track.

An exclusion removes inherited access. It does not cancel a direct collaborator or ownership grant. If you intend to remove access completely, review the full access snapshot and remove the direct grants as well.

Share links and invitations are different paths. A link may be redeemable, revoked, or limited according to its configuration. Invitations identify a recipient and a role. Public reading is a narrow configured surface; it does not grant editing or access to private neighboring resources.

Use “Shared with me” and invitations to find resources others have offered. If access is revoked, an already open tab or existing AI conversation must not retain the revoked authority.

## 10. Work with the resident AI

Tell the resident what outcome you want and which workspace or resource matters. Ask for sources when the answer affects a decision. Distinguish exploration, drafting, and execution in your request.

Useful examples:

> “Explain how this App is organized. Describe its Tracks and the relations between them.”

> “Draft a new request using the facts below. Leave unknown fields empty and let me review it.”

> “Design a review process with submissions, decisions, and evidence. Show me the proposed model before creating it.”

> “Summarize the open records I can access. Link each record and call out missing information.”

The resident uses available capabilities and current permissions. It may need clarification, approval, credentials, or a capability your installation does not expose. A skill describes a procedure; it cannot grant the resident access to restricted data.

The default resident uses Integral AI through Pydantic AI. A host can configure another supported binding. Provider availability, model choice, and credential policy affect the experience.

## 11. Review proposed changes

Read the proposed scope and change before approving it. Confirm the target resource, important values, and any dependent steps. Human-friendly names should let you understand the proposal without interpreting opaque graph IDs.

Approval authorizes its execution path. Completion still depends on current permissions, record revisions, dependencies, and provider outcomes. Check the resulting status and open the affected records after application.

If a result is uncertain, avoid repeating the request immediately when it could duplicate an external effect. Inspect receipts or ask for reconciliation. Rejecting or allowing a proposal does not establish blanket authority over later actions.

A public approval route for general bounded autonomous work is not currently a qualified product feature. Do not treat an ordinary chat approval as a grant for unattended work with an unlimited budget.

## 12. Conversation continuity

Return to a saved thread to revisit its product transcript. Context may include the page or resource from which the interaction started, but current access is checked again as tools operate.

A provider's internal session, the product transcript, and durable worker state are different things. A conversation surviving reload does not prove an interrupted action resumed. Durable native chat is off by default and requires operator qualification when enabled.

If a running request appears stuck, inspect the supported status and cancellation controls. Reloading the page is not evidence that the backend canceled the work. Use the actual result or work status before deciding whether to retry.

## 13. Models, credentials, and speech

Your host may provide a model key, permit your own key, or use a hybrid policy. Use the model-credential settings offered by the installation. Server-side storage and provider validation apply; never paste a secret into an ordinary record or shared conversation as a configuration shortcut.

Where speech input is enabled, transcription turns speech into editable text. Inspect the text before submitting important instructions. Streaming and upload transcription have provider and configuration requirements; speech input is not a promise of an autonomous voice operator.

Usage information can include provider-reported values, calculated estimates, or unavailable cost. These should remain distinguishable. An unavailable price does not mean a request was free.

## 14. Evolve an App safely

Explain the change you need in terms of work: a new record type, missing relation, state transition, or useful view. Review the proposed model and its effects on existing records.

Drafts preserve a place to inspect structure before publication. Breaking changes need supported migration operations. During an active migration, writes may be blocked to keep the schema transition coherent. Review migration status and failed-item diagnostics instead of forcing a repeat publish.

A library update does not automatically rewrite every installed instance. Upgrades and merges need their own review. A forced publish is a destructive escape that can leave existing records requiring manual repair; it is not a general fix for a validation error.

## 15. When something needs attention

- **Cannot sign in:** check the correct installation and account, then use password recovery if configured. Email verification itself does not gate login.
- **Cannot see a record:** check workspace, filters, and current resource access.
- **Cannot edit structure:** an inherited role may permit record editing but not schema curation; request a direct admin grant where appropriate.
- **AI cannot run a tool:** inspect capability availability, provider setup, policy, and current access.
- **A widget is unavailable:** inspect its data source; do not interpret the failure as an empty result.
- **A save or approval is uncertain:** inspect the resulting record, status, and receipts before repeating it.
- **A migration failed:** review diagnostics and retry the supported failed operations through the migration controls.

For operators, continue with [deployment](../ops/DEPLOY.md) and [qualification](../ops/QUALIFICATION.md). For builders, use the [App quickstart](../developer/quickstart.md). For the complete architectural argument, read the [white paper](../product/WHITE_PAPER.md).
