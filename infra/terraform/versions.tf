# Target deployment for Caduceus on AWS. A skeleton: it describes the network, data and compute layout the
# spec commits to (sections 9 and 12) and passes a static policy scan, but it has not been applied. The
# service's Postgres repository, which RDS below is for, is on the roadmap; today the API stores state in
# SQLite. See infra/terraform/README.md.

terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State holds resource identifiers, never PHI. Configure the bucket at init time:
  #   terraform init -backend-config="bucket=..." -backend-config="key=caduceus/terraform.tfstate"
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = "caduceus"
      environment = var.environment
      data_class  = "phi"
    }
  }
}
