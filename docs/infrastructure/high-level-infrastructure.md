# Integral high-level infrastructure

**Status:** Decided
**Date:** 2026-09-25
**Decision:** Amazon ECS on Fargate. See [comparison.md](comparison.md).

This is Integral's version of the ECS reference poster, "ECS SaaS - High-level
infrastructure" ([saas-reference-architecture-ecs](https://github.com/aws-samples/saas-reference-architecture-ecs)).
The layout is the same: people across the top, a control plane on the left,
an application plane on the right, and three placement tiers. The boxes are
Integral's.

The sample's Product and Order services are one API here. Documents, CRM, and
Sales are packages inside that image. The sample's DynamoDB tables are Aurora
PostgreSQL with `pgvector`.

```mermaid
flowchart TB
  tenant[Tenant]
  app[Integral application]
  jwt[Integral accounts and JWT]
  console[Operator console]
  provider[Integral provider]

  tenant --- app --- jwt --- console --- provider

  subgraph control ["Integral control plane. One Fargate task."]
    direction TB
    onboard[Tenant onboarding]
    cells[Cell management]
    billing[Entitlement and billing]
    ops[Operator admin]
    hub[Cell provisioner]
    cpdb[(Control-plane Aurora)]
    stripe[Stripe]

    onboard --> hub
    cells --> hub
    billing --> hub
    ops --> hub
    hub --> cpdb
    hub --> stripe
  end

  jwt --> hub

  subgraph appplane [ECS application plane]
    direction TB

    subgraph edge [Edge. Shared by every tier.]
      direction LR
      alb[Application Load Balancer]
      cdn[CloudFront]
      spa[S3 static SPA]
      alb --- cdn --- spa
    end

    subgraph images [Image pipeline. Same digest on every tier.]
      direction LR
      gha[GitHub Actions]
      ecr[ECR]
      gha --> ecr
    end

    subgraph tiers [Placement]
      direction LR

      subgraph basic [Basic pooled. Shared cell. Stays warm.]
        direction TB
        pool[Tenants 1 to n]
        apiPool[API task minimum 1]
        dbPool[(Shared Aurora minimum 0.5 ACU)]
        s3pool[S3 prefix per tenant]
        pool --> apiPool --> dbPool
        apiPool --> s3pool
      end

      subgraph advanced [Advanced. Service siloed. May sleep.]
        direction TB
        subgraph adv1 [Tenant 1]
          direction TB
          apiA[API task minimum 0]
          dbA[(Aurora pauses at 0 ACU)]
          s3a[S3 prefix]
          apiA --> dbA
          apiA --> s3a
        end
        subgraph advN [Tenant n]
          direction TB
          apiN[API task minimum 0]
          dbN[(Aurora pauses at 0 ACU)]
          s3n[S3 prefix]
          apiN --> dbN
          apiN --> s3n
        end
      end

      subgraph premium [Premium. Own account. Held.]
        direction TB
        subgraph prem1 [Tenant 1 account]
          direction TB
          apiP1[API task]
          dbP1[(Own Aurora)]
          apiP1 --> dbP1
        end
        subgraph premN [Tenant n account]
          direction TB
          apiPn[API task]
          dbPn[(Own Aurora)]
          apiPn --> dbPn
        end
      end
    end
  end

  hub --> alb
  ecr --> apiPool
  ecr --> apiA
  ecr --> apiN
  alb --> apiPool
  alb --> apiA
  alb --> apiN
```

## How each sample box maps

| Sample box | Integral box |
| --- | --- |
| Tenant, SaaS application, admin console, SaaS provider | Same roles. The tenant uses the hosted app. The provider operates it. |
| Amazon Cognito | Integral accounts and JWT. IAM is only for the AWS console. |
| Four Lambda functions in the control plane | Four jobs inside one always-on Fargate task. |
| Amazon EventBridge | The cell provisioner. It calls ECS, Aurora, Secrets Manager, the Cloudflare DNS API, and the cell's entitlement API. |
| Control-plane DynamoDB | Control-plane Aurora. Customer graphs stay out of it. |
| API Gateway and PrivateLink | The Application Load Balancer, so chat sockets keep a long idle timeout. |
| CloudFront and the static bucket | The Vite SPA on S3 and CloudFront. |
| Step Functions and CodeBuild | GitHub Actions pushing one image to ECR. Onboarding a tenant does not build an image. |
| Basic pooled Product and Order, shared DynamoDB | One shared API task and one shared Aurora database for tenants 1 to n. Minimum 1 task, Aurora minimum 0.5 ACU. |
| Advanced, a service per tenant | One Fargate service and one Aurora cluster per billing customer, on the same ECS cluster and the same load balancer. Minimum 0 tasks. Aurora can pause at 0 ACU. |
| Premium, a cluster per tenant | Held. When a contract requires a hard boundary, that customer gets their own AWS account, still running the same image. A cluster per tenant inside the shared account is the layout to avoid. |

Basic and advanced are the tiers to build. Premium is on the diagram so the
reference stays recognizable. It is not part of the first hosted environment.
Signup lands on the basic cell. A move to advanced is an operator action:
copy the graph, the S3 prefix, and the cell's keys together.
