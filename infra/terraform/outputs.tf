output "url" {
  description = "Load balancer DNS name; point the clinic's DNS record at it."
  value       = aws_lb.main.dns_name
}

output "audit_archive_bucket" {
  value = aws_s3_bucket.audit.id
}

output "weights_bucket" {
  description = "Stage model weights here under <model_name>/ before the model server boots."
  value       = aws_s3_bucket.weights.id
}

output "model_server_private_ip" {
  value = aws_instance.model.private_ip
}
