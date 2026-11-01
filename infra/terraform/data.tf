# Encryption keys, the database, the audit archive and the model-weights bucket.

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "data" {
  description             = "Caduceus data at rest: database, audit archive, logs, secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdministration"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "CloudWatchLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "data" {
  name          = "alias/${local.name}-data"
  target_key_id = aws_kms_key.data.key_id
}

# --- Database: Postgres, for the cases and the audit log ---------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.isolated[*].id
}

resource "aws_db_parameter_group" "postgres" {
  name   = "${local.name}-pg17"
  family = "postgres17"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name  = "log_connections"
    value = "1"
  }
  parameter {
    name  = "log_disconnections"
    value = "1"
  }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres, from the API tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from API tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.api.id
}

resource "aws_db_instance" "main" {
  #checkov:skip=CKV2_AWS_30:Statement logging would write PHI from query parameters into CloudWatch. The hash-chained audit log is the record of what happened.
  identifier                          = local.name
  engine                              = "postgres"
  engine_version                      = "17"
  instance_class                      = "db.t4g.medium"
  allocated_storage                   = 50
  storage_encrypted                   = true
  kms_key_id                          = aws_kms_key.data.arn
  db_name                             = "caduceus"
  username                            = "caduceus_admin"
  manage_master_user_password         = true
  master_user_secret_kms_key_id       = aws_kms_key.data.arn
  iam_database_authentication_enabled = true
  db_subnet_group_name                = aws_db_subnet_group.main.name
  vpc_security_group_ids              = [aws_security_group.db.id]
  parameter_group_name                = aws_db_parameter_group.postgres.name
  publicly_accessible                 = false
  multi_az                            = true
  backup_retention_period             = 35
  deletion_protection                 = true
  copy_tags_to_snapshot               = true
  auto_minor_version_upgrade          = true
  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]
  monitoring_interval                 = 60
  monitoring_role_arn                 = aws_iam_role.rds_monitoring.arn
  performance_insights_enabled        = true
  performance_insights_kms_key_id     = aws_kms_key.data.arn
  skip_final_snapshot                 = false
  final_snapshot_identifier           = "${local.name}-final"
}

# --- Audit archive: object lock in compliance mode, so no one (including the account root) can alter it -----

resource "aws_s3_bucket" "audit" {
  #checkov:skip=CKV_AWS_144:Single-region skeleton; cross-region replication of the archive is a production follow-up.
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from the archive.
  bucket_prefix       = "${local.name}-audit-"
  object_lock_enabled = true
}

resource "aws_s3_bucket_object_lock_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    default_retention {
      mode  = "COMPLIANCE"
      years = var.audit_retention_years
    }
  }
}

resource "aws_s3_bucket" "weights" {
  #checkov:skip=CKV_AWS_144:Model weights are public artifacts and can be re-staged; no replication needed.
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from the weights bucket.
  bucket_prefix = "${local.name}-weights-"
}

# Access logs for the two buckets above and the load balancer. ALB log delivery requires SSE-S3.
resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_18:This is the access-log bucket; logging it to itself would loop.
  #checkov:skip=CKV_AWS_144:Access logs are not replicated in the skeleton.
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from the log bucket.
  #checkov:skip=CKV_AWS_145:ALB access-log delivery only supports SSE-S3, not SSE-KMS.
  bucket_prefix = "${local.name}-logs-"
}

locals {
  kms_buckets = { audit = aws_s3_bucket.audit, weights = aws_s3_bucket.weights }
  all_buckets = { audit = aws_s3_bucket.audit, weights = aws_s3_bucket.weights, logs = aws_s3_bucket.logs }
}

resource "aws_s3_bucket_versioning" "all" {
  for_each = local.all_buckets
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "all" {
  for_each                = local.all_buckets
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kms" {
  for_each = local.kms_buckets
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "kms" {
  for_each      = local.kms_buckets
  bucket        = each.value.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "s3/${each.key}/"
}

resource "aws_s3_bucket_lifecycle_configuration" "all" {
  for_each = local.all_buckets
  bucket   = each.value.id
  rule {
    id     = "tidy"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
    noncurrent_version_expiration {
      noncurrent_days = each.key == "audit" ? 2555 : 90
    }
  }
}

data "aws_elb_service_account" "main" {}

# Every bucket refuses plain HTTP.
resource "aws_s3_bucket_policy" "kms" {
  for_each = local.kms_buckets
  bucket   = each.value.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [local.tls_only[each.key]]
  })
}

# The log bucket also lets the load balancer write its access logs.
resource "aws_s3_bucket_policy" "logs" {
  bucket = aws_s3_bucket.logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      local.tls_only["logs"],
      {
        Sid       = "AlbAccessLogs"
        Effect    = "Allow"
        Principal = { AWS = data.aws_elb_service_account.main.arn }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.logs.arn}/alb/*"
      }
    ]
  })
}

locals {
  tls_only = { for k, b in local.all_buckets : k => {
    Sid       = "TlsOnly"
    Effect    = "Deny"
    Principal = "*"
    Action    = "s3:*"
    Resource  = [b.arn, "${b.arn}/*"]
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  } }
}

# --- Secrets ---------------------------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "deid_key" {
  #checkov:skip=CKV2_AWS_57:Rotated by a runbook procedure; stamps are made and checked within one request, so rotation never strands data. No Lambda rotator.
  name_prefix = "${local.name}-deid-key-"
  description = "HMAC key that stamps de-identified text for the external model route"
  kms_key_id  = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret" "external_api_key" {
  #checkov:skip=CKV2_AWS_57:Vendor API key; rotated in the vendor console per the runbook.
  name_prefix = "${local.name}-external-api-key-"
  description = "API key for the external model (de-identified text only). Leave empty to keep every call in-VPC."
  kms_key_id  = aws_kms_key.data.arn
}
