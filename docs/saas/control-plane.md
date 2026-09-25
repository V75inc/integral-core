# Control plane

**Status:** Decided
**Date:** 2026-09-25
**Decision:** The control plane is one Fargate service on the same ECS cluster. See [comparison.md](comparison.md).

The control plane places customers onto cells and keeps commercial state.
It is not the product API, and it is not the AWS SaaS Builder Toolkit from
the ECS reference.

The product API already knows users, workspaces, packages, and
entitlements. The control plane knows which cell holds a billing customer,
whether that cell is awake, and what Stripe last said. Customer graphs stay
in the cell database. If the control plane is down, a cell that is already
running keeps serving. Signup, a tier move, and a webhook pause wait.

## What it owns

| Record | Why it lives here |
| --- | --- |
| Billing account | The commercial subject. A personal workspace gets a personal account. An organization can put production, sandbox, and client workspaces under one account. This matches the foundation doc. |
| Cell | Shared or siloed, region, hostname, ECS service, Aurora endpoint, secret ARN, S3 prefix, desired-count policy. |
| Placement | Which billing account is on which cell. Changing it is a migration, not an update in place. |
| Stripe inbox | Verified, deduplicated webhook events and the last reconcile. Stripe is the financial source of truth. |
| Entitlement intent | The plan, the commercial App keys, and any manual override the cell should be enforcing. |

The cell keeps the fast entitlement row the API already checks at install
and resume. The control plane writes that row through the cell's admin
API. A webhook delay must not be the only thing between a lapsed
subscription and a running App, so the cell enforces the projection it
already has, and a periodic reconcile repairs missed events.

## What it runs as

One Fargate service in the same cluster, desired count 1, on the same
load balancer, on an operator hostname. It has its own small Aurora
cluster so a customer dump never contains the tenant registry.

It calls, with a task role:

- ECS, to create or update a service and to set desired count.
- RDS, to create or pause a siloed Aurora cluster.
- Secrets Manager, to mint the cell's JWT secret and credential key.
- The Cloudflare DNS API, to publish the hostname. This is the one
  non-AWS call, and it matches the DNS-only constraint.
- The cell admin API, to grant or pause entitlements.
- Stripe, inbound, for signed webhooks.

EventBridge Scheduler is the clock for staging sleep and for drainable
work tasks. It calls ECS directly. There is no Lambda in that chain.

## What onboarding actually does

**Shared cell.** The common case.

1. Create the billing account and the placement row.
2. Create the organization workspace on the shared cell if signup did not
   already do it there.
3. Grant trial entitlements.
4. The customer installs Apps through the catalog. Install checks the
   entitlement. The control plane does not install packages by rebuilding
   infrastructure.

**Siloed cell.** An operator action, or a plan that includes it.

1. Create the Aurora cluster, the secret, the S3 prefix, and the ECS
   service with desired count 1.
2. Publish the hostname.
3. Copy the graph and the attachment prefix. Copy the keys with them.
4. Point placement at the new cell.
5. Set the service's minimum to 0 so it can sleep.

That is the whole provisioner. The ECS sample's CodeBuild-per-tenant and
the EKS sample's `Tenant` manifest are both larger than this, because
their applications are a set of microservices and cloud resources per
tenant. Integral is one image and one database.

## What it refuses

- A Lambda in front of signup or in front of the API wake path. Wake is a
  CloudWatch alarm on the load balancer setting ECS desired count to 1.
  The first request during sleep gets a retry page.
- Cognito, API Gateway, and a DynamoDB tenant table. Those are the SaaS
  Builder Toolkit's control plane. Integral already has the directory and
  the graph.
- Argo CD as the place a tenant is declared. Git is the source for the
  image and for the CloudFormation template that defines the platform. A tenant is a row,
  because a tenant has runtime state (Stripe, placement, keys) that a Git
  commit is a poor home for.
- Per-tenant image builds. Every cell runs the same digest. Tier changes
  placement and data stores, not the binary.

## First slice

The first hosted environment is the shared cell alone, plus the control
plane's own small Aurora cluster. Entitlements are still granted through the
existing admin API. Add the siloed-cell provisioner when the first customer
needs their own database. Add the Stripe inbox before the first charge. The
API stays at one task until ADR-005 is done. The platform itself is the
CloudFormation template in [cloudformation.md](cloudformation.md).
