output "instance_id" {
  description = "EC2 instance ID. Use with `aws ssm start-session --target <id>` for a shell."
  value       = aws_instance.outreach.id
}

output "alb_url" {
  description = "Raw ALB HTTPS URL. Listener rules intentionally return 404 unless the canonical host header matches."
  value       = "https://${aws_lb.outreach.dns_name}"
}

output "alb_dns_name" {
  description = "ALB DNS hostname. Raw ALB requests are denied by listener host-header rules."
  value       = aws_lb.outreach.dns_name
}

output "ssm_session_command" {
  description = "Copy-paste command to open a shell on the instance via SSM Session Manager."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.outreach.id}"
}
