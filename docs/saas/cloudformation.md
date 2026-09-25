# First-launch CloudFormation

**Status:** Decided
**Date:** 2026-09-25

The platform template is
[integral-saas.yaml](integral-saas.yaml) in this folder.
It builds the first hosted environment in us-east-1: one ECS cluster, the
shared cell, and the control plane's own Aurora cluster. A siloed cell is a
later addition. This file is the deploy guide for that template.

An example parameter file is
[integral-saas.parameters.example.json](integral-saas.parameters.example.json).

## What the template creates

| Resource | Role |
| --- | --- |
| VPC, two public subnets, internet gateway | One network. No NAT gateway. Tasks get a public IP. Inbound traffic is allowed only from the load balancer. |
| S3 gateway endpoint | Attachment traffic to S3 stays on the AWS network. |
| ECS cluster `integral` | Fargate. Container Insights is off. |
| API service | Shared cell. Desired count starts at 0 and is capped at 1. Stop-then-start deploys (minimum healthy 0, maximum 100) while ADR-005 stands. |
| Control-plane service | Same cluster, desired count 0 until that image exists. Its database is created now. |
| Two Aurora PostgreSQL Serverless v2 clusters | `integral` for the product, minimum 0.5 ACU. `integral_control` for placement and billing, minimum 0.5 ACU. Neither instance is publicly accessible. Deletion protection is on. |
| Secrets Manager | JWT signing key, credential encryption key, and the RDS-managed database passwords. |
| S3 | One bucket for attachments, one private bucket for the SPA. |
| CloudFront | Serves the SPA. API traffic does not go through it. Idle timeout on the load balancer is 4000 seconds for chat sockets. |
| Application Load Balancer and WAF | Host `ApiHostname` forwards to the API. Host `cp.<mail domain>` forwards to the control plane. The common rule set counts `SizeRestrictions_BODY` so uploads are not blocked at 8 KB. |
| ECR | `integral/api` and `integral/control-plane`. |
| SES domain identity | Easy DKIM. The three CNAMEs are stack outputs. Add them in Cloudflare. |
| GitHub deploy role | Created only when `GitHubOrg` is set. OIDC, no long-lived keys. |

## What it leaves out

These are in the sample diagrams or in the later scale sequence. They are not
in this template.

- Siloed cells, and a cluster or account per tenant.
- Lambda, API Gateway, PrivateLink, Cognito, EventBridge as a tenant bus, DynamoDB, Step Functions, and CodeBuild.
- A NAT gateway, ElastiCache, and RDS Proxy.
- Cloudflare records. The stack outputs the targets. You create grey-cloud CNAMEs yourself.
- The control-plane application. The database, the repository, and a service at count 0 are ready for it.
- `CREATE EXTENSION vector`. Aurora does not turn `pgvector` on by itself. The SQL file is [enable-pgvector.sql](enable-pgvector.sql).

## Before you deploy

1. Confirm the engine version exists in the account. The default is `16.6`.

   ```bash
   aws rds describe-db-engine-versions \
     --engine aurora-postgresql \
     --query "DBEngineVersions[?contains(SupportedEngineModes, 'provisioned')].EngineVersion" \
     --region us-east-1
   ```

   Pass a listed 16.x version as `AuroraEngineVersion` if `16.6` is not there.

2. Request an ACM certificate in us-east-1. It has to cover all three names:

   - `AppHostname`, for example `app.example.com`
   - `ApiHostname`, for example `api.example.com`
   - `cp.<MailDomain>`, for example `cp.example.com`

   Validation CNAMEs go in Cloudflare, grey-cloud. Wait until the certificate
   status is Issued. Pass its ARN as `CertificateArn`. The stack does not
   create the certificate, so a missing DNS record cannot leave the stack
   stuck in `CREATE_IN_PROGRESS`.

3. Copy the example parameters file and replace the hostnames, the
   certificate ARN, and the mail domain.

## Deploy

From `integral-core`, with credentials for the target account:

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name integral-saas \
  --template-file docs/saas/integral-saas.yaml \
  --parameter-overrides file://docs/saas/integral-saas.parameters.json \
  --capabilities CAPABILITY_NAMED_IAM
```

`file://` parameter overrides are not accepted by every AWS CLI version. If
the command rejects the file, expand the keys on the command line.

The first deploy finishes with both services at desired count 0. Aurora
starts billing at the 0.5 ACU floor as soon as each cluster exists. The load
balancer starts billing as soon as it exists. Fargate stays at zero until you
raise a count.

## Cloudflare records

Grey-cloud. Proxy off.

| Name | Type | Target |
| --- | --- | --- |
| `app` | CNAME | Stack output `CloudFrontDomainName` |
| `api` | CNAME | Stack output `LoadBalancerDnsName` |
| `cp` | CNAME | Stack output `LoadBalancerDnsName` |
| three DKIM names | CNAME | Stack outputs `SesDkimTokenName*` and `SesDkimTokenValue*` |

Add the SPF and DMARC TXT records SES shows for `MailDomain` when you are
ready to send.

## Image, extension, and the first task

Push the API image to the `ApiRepositoryUri` output at the `ImageTag` you
set (default `bootstrap`). Then enable `pgvector` through the Data API, which
reaches the private database without a bastion and without Lambda:

```bash
aws rds-data execute-statement \
  --region us-east-1 \
  --resource-arn "<SharedDbClusterArn>" \
  --secret-arn "<SharedDbSecretArn>" \
  --database integral \
  --sql "CREATE EXTENSION IF NOT EXISTS vector"
```

Raise the API count only after that image exists:

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name integral-saas \
  --template-file docs/saas/integral-saas.yaml \
  --parameter-overrides file://docs/saas/integral-saas.parameters.json ApiDesiredCount=1 \
  --capabilities CAPABILITY_NAMED_IAM
```

Publish the Vite build to the SPA bucket and invalidate CloudFront:

```bash
aws s3 sync frontend/dist s3://<SpaBucketName> --delete
aws cloudfront create-invalidation --distribution-id <id> --paths "/*"
```

The control-plane count stays 0 until that repository has an image and
`/health` responds on port 4000.

## Task identity

The API task role can read and write the attachments bucket. The template
does not set `JVSPATIAL_S3_ACCESS_KEY` or `JVSPATIAL_S3_SECRET_KEY`. The AWS
SDK on the task is expected to use the task role. If the process refuses to
start because those variables are empty, that is a product change to make
before the first real task, not a reason to mint an IAM user key.

`WEB_CONCURRENCY` is `1`. Do not raise `ApiDesiredCount` above 1 until the
shared turn registry and websocket fan-out from ADR-005 both exist.

## Tear down

Deletion protection is on for both Aurora clusters, and the buckets and
secrets are retained. To delete the stack, turn deletion protection off on
both clusters, empty the buckets you are willing to drop, then delete the
stack. Retained secrets and buckets stay in the account.
