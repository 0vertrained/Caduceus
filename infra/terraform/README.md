# Terraform: target AWS deployment

A skeleton of the deployment the spec commits to. It has **not been applied**. It passes a static policy scan
(Checkov: 292 passed, 0 failed, 12 skipped, each skip with its reason next to the resource), and CI runs
`terraform fmt -check`, `terraform validate` and the scan on every push.

## Layout

```
                     internet
                        |
                  [ WAF + ALB ]  public subnets, HTTPS only (TLS 1.2+), access logs to S3
                        |
              [ ECS Fargate: API + UI ]  app subnets; NAT egress for the de-identified external route
               /        |          \
   [ RDS Postgres ]  [ GPU: vLLM ]  [ VPC endpoints: S3, ECR, KMS, Secrets Manager, Logs, SSM ]
     isolated subnets: no route to the internet in either direction
```

* **Isolation of the model.** The GPU instance accepts connections only from the API tasks' security group,
  loads weights from S3 through the gateway endpoint, pulls the vLLM image from this account's ECR, runs with
  request logging off, and is reached by operators through SSM Session Manager. It has no SSH, no public IP, and
  no route out.
* **Encryption.** One customer-managed KMS key (rotation on) for the database, Performance Insights, the audit
  archive, the weights bucket, log groups and secrets. RDS forces TLS. EBS volumes are encrypted.
* **The audit archive** is an S3 bucket with Object Lock in compliance mode for six years (the HIPAA
  documentation retention period, 45 CFR 164.316(b)(2)). The API's role can put objects there and nothing else.
* **Identity.** The API runs in OIDC mode against the clinic's identity provider; development sign-in is never
  enabled here. The de-identification key and the optional external model key are injected from Secrets Manager.
* **Logging without PHI.** Database statement logging is off on purpose (query parameters would carry PHI); WAF
  keeps no sampled requests and redacts the Authorization header; flow logs record addresses and ports only.

## Before an apply

1. The service's Postgres repository has to land first (state is in SQLite today; see ADR 4).
2. Mirror the vLLM image into ECR and stage the model weights under `s3://<weights_bucket>/<model_name>/`.
3. Narrow the load balancer's ingress from `0.0.0.0/0` to the clinic's egress ranges.
4. Confirm every service used for PHI is on the current AWS HIPAA-eligible services list and covered by the BAA.

```
terraform init -backend-config="bucket=<state-bucket>" -backend-config="key=caduceus/terraform.tfstate"
terraform plan -var api_image=... -var vllm_image=... -var certificate_arn=... -var oidc_issuer=...
```
