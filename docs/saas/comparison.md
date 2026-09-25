# Why ECS

**Status:** Decided
**Date:** 2026-09-25
**Decision:** Amazon ECS on Fargate. EKS is rejected.

This is the record of that choice. Two public AWS samples were reviewed
against Integral as it actually runs:
one API image, one web image, Postgres with `pgvector`, workspace-scoped
access, and commercial Apps installed as packages.

| | [ECS SaaS reference](https://github.com/aws-samples/saas-reference-architecture-ecs) | [SaaS on EKS](https://github.com/aws-samples/sample-saas-on-eks) | Integral |
| --- | --- | --- | --- |
| Orchestrator | Amazon ECS, CDK | Two EKS clusters, Terraform, Argo CD, ACK, KRO | Docker Swarm today. Decided target: one ECS cluster on Fargate. |
| Control plane | AWS SaaS Builder Toolkit: Lambda, EventBridge, Cognito, API Gateway | Argo CD on the control cluster, IAM Identity Center, CodeCommit | A small always-on task plus a control-plane database. Product login stays Integral JWT. |
| How a tenant appears | CodeBuild provisions a stack from a tier template | A YAML `Tenant` custom resource. KRO expands it into a namespace, IAM, network policy, and data stores. | A billing account placed on a cell. Workspaces, Apps, and entries already exist inside the cell. |
| Basic tier | Shared ECS services, shared DynamoDB | Shared S3 and DynamoDB, IAM partition scope, shared node pool | Shared cell: one API task, one Aurora database, many billing customers |
| Advanced / pro tier | Shared cluster, a dedicated ECS service per tenant | Dedicated S3, DynamoDB, and Karpenter node pool | Siloed cell: own Fargate service and own Aurora cluster, same ECS cluster and load balancer |
| Premium tier | A dedicated ECS cluster per tenant | Not a separate tier. Pro is the strong isolation. | Held. An own cluster is a contract exception, after an own AWS account. |
| Application data | DynamoDB per microservice (order, product, user) | DynamoDB and S3 | Aurora PostgreSQL and S3 |
| Identity | Amazon Cognito for tenants and admins | IAM Identity Center for Argo CD | Integral users and JWT. IAM is for AWS operators only. |
| Delivery | CDK plus CodeBuild image builds | Git push to CodeCommit, Argo CD sync, CodeBuild to ECR | CDK in Python for the platform. GitHub Actions to ECR, then an ECS service update. |
| Idle cost | A cluster of always-on services, plus Lambda on each onboard | Two EKS control planes at [$0.10 per cluster-hour](https://aws.amazon.com/eks/pricing/) each, about $73 a month before nodes, paid twice | Shared cell stays warm. A siloed cell can go to zero tasks and zero Aurora ACUs. |

## Why ECS was chosen

The ECS reference separates a control plane from an application plane, and it
offers pooled and siloed placement on one cluster. That is the placement
Integral needs. A hosted customer lands on the shared cell. A customer who
needs their own database, a noisy-neighbor boundary, or a clean dump and
delete gets a siloed cell. Both cells run the same images.

The compute model matches a container Integral already builds. There is no
cluster-hour fee. A service desired count of 0 is a real idle state. Aurora
Serverless v2 with a minimum of 0 ACU pauses when nothing is connected, which
is the database half of that idle state. Fargate runs the task without a
Kubernetes version to upgrade.

## Why EKS was rejected

The EKS sample is a workshop for a platform team. Its control cluster runs
the only Argo CD. Its data cluster runs tenant workloads. ACK creates AWS
resources from Kubernetes. KRO turns a short tenant manifest into a full
stack. That design fits a product whose tenant is a YAML file and whose data
is DynamoDB plus S3 with IAM conditions.

Integral's isolation boundary is the graph in Postgres, reached through
`X-Integral-Scope` and `resolve_role`. A namespace does not separate that
graph. A `Tenant` custom resource would be a second source of truth next to
the billing account and the entitlement rows that already exist.

GitHub Actions already builds images. Argo CD, ACK, and KRO would be a second
delivery system beside that pipeline. The API stays one replica until ADR-005
is done, so the platform fee would not buy a second process. One ECS service
per cell is the smaller system for the same placement decision. This rejection
stands for the hosted product. It is not a placeholder pending a later
Kubernetes migration.

## What to leave in the samples

These pieces solve a problem Integral does not have. Adopting them would
replace working product code with a reference app.

**From the ECS reference**

- The SaaS Builder Toolkit control plane. It is Lambda, API Gateway, and
  DynamoDB. The control plane here is a container and Aurora, so idle cells
  can sleep without a function in the request path.
- Amazon Cognito as the tenant directory. Integral already has users, email
  verification, workspace membership, and JWT.
- API Gateway plus PrivateLink in front of the API. Chat sockets want an
  Application Load Balancer with a long idle timeout. The API stays off
  CloudFront for the same reason.
- A CodeBuild project that synthesizes a tenant stack on every onboard.
  Placing a customer on the shared cell is a row and an entitlement grant.
  Placing them on a siloed cell is an ECS service, an Aurora cluster, a
  secret, and a DNS record.
- Product, Order, and User as separate ECS services. Documents, CRM, and
  Sales are packages mounted into Core (`INTEGRAL_PACKAGE_PATHS`), installed
  per workspace. They are not separately scaled processes.
- DynamoDB as the system of record. The graph, permissions, and `pgvector`
  retrieval are Postgres.

**From the EKS sample**

- A second cluster whose only job is Argo CD.
- CodeCommit as the Git remote. Source stays on GitHub.
- The Lambda that aggregates per-tenant usage every five minutes. Usage for
  billing is the Integral ledger described in the foundation doc. Cost
  allocation starts as resource tags and Cost Explorer.
- Per-tenant Karpenter node pools. A paused Fargate task is the cheaper
  isolation boundary for a customer who is idle overnight.
- IAM Identity Center as the product login. Use it for the AWS console and
  for whoever operates the control plane. Customer users sign in to Integral.

## What is worth copying as an idea

- One region is one VPC, one load balancer, one NAT (or none, at the start),
  and one cluster. A load balancer, NAT gateway, or VPC per customer is the
  expensive mistake.
- Tier is a placement decision recorded by the control plane, not a fork of
  the application.
- Siloed data is a database you can dump, restore, and delete on its own.
- Image build is a pipeline into a registry. Tenant onboarding does not
  rebuild the image.
- Cost is attributable to a customer. Tags on the siloed service, cluster,
  and bucket prefix are enough to start. A CUR, Glue, and Athena path can
  come later, the way the EKS sample does, without the Lambda aggregator.
