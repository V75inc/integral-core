# Organization and HRM architecture

**Status:** Proposed product architecture
**Purpose:** Establish clear ownership for organization, workforce, payroll,
and platform concerns in Integral.

## Decision summary

Integral should not model official company information as HR-owned data.
Instead, it should use a foundational **Organization** App for legal-entity
master data and a separate **HRM** App for workforce and employment-lifecycle
data. Payroll owns compensation and payment data. Integral itself supplies
cross-cutting capabilities such as attachments, notifications, access control,
and audit history.

| Concern | Architectural home |
| --- | --- |
| Legal company, legal entities, registrations, corporate addresses and branches | **Organization App** |
| Employees, departments, positions, leave, onboarding and personnel records | **HRM App** |
| Compensation, payment instructions, pay runs, payslips and statutory payroll settings | **Payroll App** |
| Candidate pipeline, interviews, offers and external candidates | **Recruitment App** later, if needed |
| Clock-in/out, rosters, shifts and time-clock integrations | **Time & Attendance App** later, if needed |
| Attachments, notifications, audit trail, sharing/access and generic exports | **Integral platform** |

## Organization App

The App should be named **Organization**. “Company Profile” is the primary
user-facing management view, not the canonical domain object. The canonical
object is a **Legal Entity**, which supports subsidiaries, multiple tax
registrations, and multiple jurisdictions without redesign.

```text
App: Organization

Track: Organization Settings
  - organization_profile

Track: Legal Entities
  - legal_entity

Track: Registrations
  - registration

Track: Addresses & Locations
  - address
```

### Organization Settings

`organization_profile` is a singleton workspace/group record containing:

- Workspace or group name
- Default legal entity
- Group logo/brand
- Default locale and timezone

### Legal Entities

`legal_entity` is the authoritative employer/company identity record:

- Legal name and trading/display name
- Entity type
- Incorporation/formation jurisdiction and date
- Operational status
- Functional currency
- Registered, billing and payroll address relations
- Optional internal entity code

### Registrations

`registration` relates to exactly one Legal Entity and models official
identifiers with their correct legal meaning:

- Registration kind: taxpayer ID, VAT/GST, employer payroll, social security,
  incorporation, business licence, and similar concepts
- Issuing jurisdiction and authority
- Official identifier value
- Effective-from and effective-to dates
- Status: active, superseded or expired
- Supporting-document attachment

Registrations should be effective-dated. Historic registrations must not be
overwritten merely because an identifier changes.

### Addresses & Locations

`address` relates to a Legal Entity and holds:

- Address role: registered, billing, operational, payroll or branch
- Structured address fields
- Effective dates and active status

The physical location is Organization master data. HRM records the employee’s
work-location assignment through a relation to it.

### Organization cross-App contract

HRM, Payroll and Finance records should reference `legal_entity` through a
declared relation. They should not duplicate a generic company-profile ID or
independently recreate legal name and tax-registration fields.

An immutable issued artifact—such as a payslip, invoice, filed return or
statutory filing—must retain a snapshot of the official identity used at the
time of issuance. Later changes to a Legal Entity must never rewrite history.

## HRM App

The HRM App owns people and employment lifecycle data. It does not own legal
company identity, compensation, banking/payment details, generic attachments,
notifications or the technical audit log.

```text
App: HRM

Track: Employees
  - employee

Track: Employment Events
  - employment_event

Track: Departments
  - department

Track: Positions
  - position

Track: Leave Setup
  - leave_type
  - leave_policy
  - leave_entitlement

Track: Leave Requests
  - leave_request

Track: Employee Documents
  - employee_document

Track: Restricted Personnel Records
  - restricted_personnel_record

Track: Onboarding
  - onboarding_template
  - onboarding_task
```

### Employees

`employee` is the workforce/personnel master record. It should contain:

- Employee ID and work email
- Contact information and emergency contact
- Date of birth and gender only where legitimately needed
- Employment type, hire date and employment status
- Department, position, manager, Legal Entity and work-location relations

It should **not** contain salary, payment or bank-account information. Those
belong to Payroll and should be visible only to the appropriate payroll group.

### Employment Events

The audit log is necessary, but it is not the employee-history model. It
answers who changed a record and when; it does not reliably represent the
business state effective on a particular date.

`employment_event` therefore captures effective-dated business events:

- Promotion, transfer, position change, department change and manager change
- Employment-status change and suspension
- Termination, with termination reason such as resignation

Each record relates to an employee and includes an effective date, reason,
previous/new values or relations, and optional supporting documents.

“On leave” is not an employee status—it is a dated Leave Request. “Resigned”
is normally a termination reason, not a current employee status.

### Organization structure in HRM

HRM owns departments, positions, manager/reporting relationships and workforce
assignments.

- `department` supports a `parent_department` relation for hierarchy and a
  `legal_entity` relation back to Organization.
- `position` supports department, level, responsibilities and approved/open
  headcount.
- Employee → Department is the authoritative assignment. Do not also maintain
  a manually editable Department → Employees list; that creates drift.

A Position is an internal approved role/headcount record. It is not the same
thing as a future job requisition.

### Leave Management

Leave remains in HRM for the MVP.

- `leave_type`: vacation, sick, parental, unpaid and similar classifications
- `leave_policy`: eligibility, accrual/entitlement and approval-routing rules
- `leave_entitlement`: employee + leave type + period + granted/carry-forward
  allowance
- `leave_request`: employee, leave type, dates, duration, reason, attachments,
  approver, decision status and decision metadata

Use a trusted server-side bundle hook/tool for duration and balance
calculation, so human, agent and API writes all apply the same rules. The MVP
views are a leave calendar, pending-approval queue, employee leave history and
leave-balance table.

### Employee Documents

Integral’s attachment facility stores the actual file. `employee_document`
adds the HR meaning and relationship:

- Employee relation
- Document type
- Issue and expiration dates
- Status and notes
- Attachment(s)

Medical certificates, disciplinary material and similarly sensitive documents
must live in `restricted_personnel_record`, or have equivalent entry-level
access protection. They should not be exposed merely because a user has broad
directory access.

### Onboarding and recruitment

Keep basic onboarding in HRM:

- `onboarding_template` for reusable checklists
- `onboarding_task` for per-employee work with owner, due date, category and
  status

Do not create a separate Recruitment App for a simple MVP. Once Integral needs
candidates, interviews, offers, interviewers, external applicant access and a
real hiring pipeline, create a **Recruitment** App. It should relate to HRM
Positions and create an Employee/Onboarding instance when a candidate is
hired.

## Platform capabilities to reuse

These are not HRM tracks or standalone HR Apps:

| Capability | Use in the architecture |
| --- | --- |
| Attachments | Store actual employee-document and evidence files against an Entry. |
| Notifications | Notify on leave submission/decision, onboarding work and document expiry. |
| Audit log | Use Integral’s unified ChangeEvent/audit trail for technical accountability. |
| Dashboard | Provide HRM views for active headcount, pending leave, expiring documents and onboarding work. |
| Employee self-service | Provide a constrained role and scoped views/actions, not another App. |
| Reports/export | Build from HRM views: directory, headcount, hires, exits, leave and absence summaries. |

## Access and privacy requirements

HRM is privacy-sensitive by design. The ordinary employee self-service
experience must be constrained to that employee’s permitted data and actions:

- View/update approved personal fields
- Submit and view their own leave requests
- View their own permitted documents

It must not expose the workforce directory’s sensitive fields, another
employee’s personnel record, restricted personnel material, salary, or payment
data. If the applicable access model cannot enforce own-record and
sensitive-record boundaries, self-service should not be exposed until it can.

Organization records should default to read access with a narrowly assigned
editor/admin group. Registrations and supporting documents need particularly
careful protection.

## Substrate implementation rules

Every app’s Tracks and Entries remain structurally rooted through the normal
`Workspace → App → Track → Entry` containment chain. Cross-domain references
such as Employee → Legal Entity, Leave Request → Employee, and Registration →
Legal Entity are declared relations/edges; they are not manually maintained
foreign-key strings or structural nesting of Entries beneath other Entries.

This arrangement gives Integral one clean source of truth for each concern:

- Organization owns legal identity and locations.
- HRM owns workforce and employment lifecycle.
- Payroll owns money and payment details.
- Integral supplies the shared operational substrate.
