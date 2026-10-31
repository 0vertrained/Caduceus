variable "region" {
  description = "AWS region. Every service used for PHI must be on the AWS HIPAA-eligible services list."
  type        = string
  default     = "us-west-2"
}

variable "availability_zones" {
  description = "Exactly two zones in the region, by name."
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b"]
  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "Give exactly two availability zones."
  }
}

variable "environment" {
  description = "Environment name, used in resource names."
  type        = string
  default     = "demo"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "api_image" {
  description = "Container image for the API and UI (the Dockerfile at the repository root), pushed to ECR."
  type        = string
}

variable "certificate_arn" {
  description = "ACM certificate for the load balancer's HTTPS listener."
  type        = string
}

variable "oidc_issuer" {
  description = "Issuer URL of the clinic's identity provider (OIDC)."
  type        = string
}

variable "oidc_audience" {
  type    = string
  default = "caduceus"
}

variable "oidc_client_id" {
  description = "The review UI's client at the clinic's identity provider: public, Authorization Code with PKCE (docs/sign-in.md)."
  type        = string
  default     = "caduceus-ui"
}

variable "model_instance_type" {
  description = "GPU instance for the in-VPC model server."
  type        = string
  default     = "g6.xlarge"
}

variable "model_name" {
  description = "Open-weight model served by vLLM, loaded from the weights bucket."
  type        = string
  default     = "meta-llama/Llama-3.1-8B-Instruct"
}

variable "vllm_image" {
  description = "vLLM server image mirrored into this account's ECR (the model subnet cannot reach public registries)."
  type        = string
}

variable "audit_retention_years" {
  description = "Object-lock retention for the audit archive. HIPAA documentation must be kept six years (45 CFR 164.316(b)(2))."
  type        = number
  default     = 6
}
