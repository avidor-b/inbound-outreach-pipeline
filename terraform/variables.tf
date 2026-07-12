variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name used as a tag and resource-name prefix."
  type        = string
  default     = "outreach"
}

variable "environment" {
  description = "Environment label used in tags (v0, staging, production)."
  type        = string
  default     = "v0"
}

variable "instance_type" {
  description = "EC2 instance type. t3.medium is the minimum that runs the full stack comfortably (server + frontend + chroma + postgres)."
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size_gb" {
  description = "EBS root volume size in GB. Docker images and host logs live here; Chroma uses its own attached EBS volume and Postgres uses RDS."
  type        = number
  default     = 40
}

variable "server_host_port" {
  description = "Host port the FastAPI server is published on. Matches SERVER_HOST_PORT in docker-compose."
  type        = number
  default     = 8080
}

variable "frontend_host_port" {
  description = "Host port the Next.js frontend is published on. Matches FRONTEND_HOST_PORT in docker-compose."
  type        = number
  default     = 3001
}

variable "alert_email" {
  description = "Email subscribed to the alerts SNS topic. AWS sends a confirmation link on first apply; click it to activate. Required — set in terraform.tfvars or via -var."
  type        = string

  validation {
    condition     = can(regex("^[^@]+@[^@]+\\.[^@]+$", var.alert_email))
    error_message = "alert_email must be a valid email address."
  }
}
