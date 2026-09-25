# Hosted Integral on AWS

**Status:** Decided
**Date:** 2026-09-25
**Decision:** Amazon ECS on Fargate. One cluster per region.

This folder is the hosting shape for Integral as a SaaS. The product boundary
(open core, commercial Apps, entitlements, Stripe) stays in
[FOUNDATION_EXTENSION_SAAS.md](../product/FOUNDATION_EXTENSION_SAAS.md).
What runs today is still Docker Swarm. See [ops/DEPLOY.md](../ops/DEPLOY.md).
The reasons for ECS, and for leaving the rest of the AWS samples behind, are
in [comparison.md](comparison.md).

## Decision

Run Integral on **Amazon ECS on Fargate**. Take the shape of the
[ECS SaaS reference](https://github.com/aws-samples/saas-reference-architecture-ecs):
one cluster, a control plane beside the product, a pooled cell for most
customers, and a siloed cell when a customer needs their own database.
Leave both sample repositories undeployed.

EKS is rejected. The
[EKS sample](https://github.com/aws-samples/sample-saas-on-eks) is a GitOps
platform for stamping Kubernetes tenants. Integral's tenant is a billing
account and a workspace graph. A namespace, a DynamoDB table, or a Karpenter
pool does not isolate that graph.

### Why ECS

1. **The software is already a container and Postgres.** The API image, the
   SPA, and Aurora with `pgvector` run as ECS services. There is no cluster
   to operate and no cluster-hour fee.
2. **Pooled and siloed placement fit on one cluster.** A shared cell is one
   service that stays at 1 task. A siloed cell is another service whose
   desired count can be 0. Aurora Serverless v2 pauses at 0 ACU once that
   task is gone. That is scale-to-zero without Lambda.
3. **EKS would bill a control plane and still run one replica.** An EKS
   control plane is [$0.10 per cluster per hour](https://aws.amazon.com/eks/pricing/)
   (about $73 a month) before any pod. The EKS sample runs two clusters, so
   that fee is paid twice. [ADR-005](../backend/adr/005-single-worker-until-shared-turn-state.md)
   pins the API to one process until turn state is shared. A second
   orchestrator does not lift that limit.
4. **The exit path stays ordinary.** The same image, `pg_dump` into any
   Postgres 16 with `pgvector`, and S3-compatible attachments. ECS, the load
   balancer, and Aurora's pause behavior are the AWS-specific parts this
   decision accepts, because they are what make placement and idle cost
   simple.

## Constraints already chosen

These came out of the two working sessions on 2026-09-25 (Kubernetes versus
a managed scheduler, then a control plane that can sleep):

- Cloudflare is DNS only. Records are grey-cloud CNAMEs. TLS, the load
  balancer, and the static site sit on AWS.
- Everything else is an AWS service.
- Idle dedicated cells can sleep without Lambda.
- Vendor-specific services are acceptable when they are cheaper, simpler, or
  easier to scale. The exit path stays ordinary: the same container image,
  `pg_dump` into any Postgres 16 with `pgvector`, and S3-compatible attachments.

## Decisions that go with ECS

| Decision | Choice |
| --- | --- |
| Region | us-east-1. A second region is a residency exception, with one writer per cell. |
| First launch | The shared cell only. A siloed cell is added when a customer needs their own database. |
| Task network | Public subnet and a public task IP. Inbound traffic is allowed only from the load balancer. One NAT gateway is a later upgrade for private egress. |
| Platform definition | AWS CDK in Python for the VPC, cluster, load balancer, and Aurora. GitHub Actions builds the image, pushes it to ECR, and updates the ECS service. |
| Control-plane database | Its own small Aurora cluster from day one, so a customer dump never contains the tenant registry. |

## How to read this

| Doc | What it answers |
| --- | --- |
| [comparison.md](comparison.md) | Why ECS was chosen, why EKS was rejected, and which sample pieces stay behind |
| [high-level-infrastructure.md](high-level-infrastructure.md) | Integral's version of the ECS high-level infrastructure poster |
| [architecture.md](architecture.md) | Diagrams of the Integral platform, the two planes, and the request path |
| [cells.md](cells.md) | Pooled cell, siloed cell, what is allowed to sleep, and the cost floor |
| [control-plane.md](control-plane.md) | What the control plane does, and why it is not the SaaS Builder Toolkit |

## What has to be true in the product first

The scheduler can move containers today. These code facts still set the
ceiling. Build them in this order:

1. Keep one API task per cell until the turn registry and websocket fan-out
   both live in shared state. That is the ADR-005 unit of work. A rolling
   deploy that overlaps two API tasks is unsafe until then. On ECS that means
   stop-then-start (minimum healthy 0 percent, maximum 100 percent).
2. Move embeddings out of `/data/integral_embeddings.db` and the change-event
   log out of `/tmp/integral_logs.db`. Fargate disk is wiped on every stop.
   Vectors belong in Aurora. Durable audit belongs in Postgres. Process logs
   go to CloudWatch.
3. Pull scheduler loops out of the API process. One leader. Work that can
   drain runs as a Fargate task that exits when the queue is empty.
4. On the shared cell, add row-level security keyed by the billing customer,
   enforced from the resolved principal. Workspace id and billing-customer id
   are different keys.
5. Finish the Stripe webhook inbox. `POST /api/entitlements/grant` stays the
   audited override for trials and contracts.
