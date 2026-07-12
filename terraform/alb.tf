# Application Load Balancer.
#
# Public access is HTTPS-only and host-header restricted to the
# canonical app FQDN from dns_cert.tf. Requests to the raw ALB DNS
# intentionally fall through to the default 404 response.

# --- ALB security group ---------------------------------------------------

resource "aws_security_group" "alb" {
  name_prefix = "${local.name_prefix}-alb-"
  description = "ALB - public HTTPS only (HTTP listener removed in Phase D)"
  vpc_id      = aws_vpc.outreach.id

  ingress {
    description = "HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "To targets in the VPC"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["10.0.0.0/16"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alb-sg"
  })

  lifecycle {
    create_before_destroy = true
  }
}

# --- ALB ------------------------------------------------------------------

resource "aws_lb" "outreach" {
  name_prefix        = "cb-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [for s in aws_subnet.public : s.id]

  enable_deletion_protection = false
  enable_http2               = true
  idle_timeout               = 120 # SSE streaming needs > default 60s

  # Reject malformed / non-RFC header fields at the ALB rather than
  # forwarding them to the backend. Closes a class of smuggling/injection
  # tricks that rely on the origin being more permissive than the ALB.
  drop_invalid_header_fields = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-alb"
  })
}

# --- Target groups --------------------------------------------------------
# One for each published port on the EC2. Both run HTTP internally.

resource "aws_lb_target_group" "server" {
  name_prefix = "cb-srv"
  port        = var.server_host_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.outreach.id
  target_type = "instance"

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }

  deregistration_delay = 15

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-tg-server"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group" "frontend" {
  name_prefix = "cb-ui"
  port        = var.frontend_host_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.outreach.id
  target_type = "instance"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 15
    matcher             = "200"
  }

  deregistration_delay = 15

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-tg-frontend"
  })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb_target_group_attachment" "server" {
  target_group_arn = aws_lb_target_group.server.arn
  target_id        = aws_instance.outreach.id
  port             = var.server_host_port
}

resource "aws_lb_target_group_attachment" "frontend" {
  target_group_arn = aws_lb_target_group.frontend.arn
  target_id        = aws_instance.outreach.id
  port             = var.frontend_host_port
}

# --- Listeners ------------------------------------------------------------
# HTTPS-only on 443. The canonical app host routes to the frontend;
# /copilotkit, /auth, /threads, /contacts, /engagements, and /pipeline
# go to the backend. The liveness probe remains available only to the
# ALB target group health checker, not as a public listener route.
# Requests sent directly to the raw ALB DNS name fall through to the
# default 404 and are not app-accessible. No HTTP listener — cleartext
# traffic is rejected at the SG, not redirected.

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.outreach.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  # Publicly trusted, DNS-validated certificate from dns_cert.tf.
  certificate_arn = aws_acm_certificate_validation.app.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

# ALB listener rules cap at 5 path values per condition, so split the
# backend routes into two rules. Wildcard patterns collapse /foo and
# /foo/* into a single value.

resource "aws_lb_listener_rule" "frontend_app" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 200

  condition {
    host_header {
      values = [local.app_fqdn]
    }
  }

  action {
    type = "forward"

    forward {
      target_group {
        arn    = aws_lb_target_group.frontend.arn
        weight = 1
      }
    }
  }
}

resource "aws_lb_listener_rule" "backend_api_core" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 100

  condition {
    host_header {
      values = [local.app_fqdn]
    }
  }

  condition {
    path_pattern {
      values = [
        "/auth*",
        "/threads*",
        "/contacts*",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.server.arn
  }
}

resource "aws_lb_listener_rule" "backend_api_engagements" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 105

  condition {
    host_header {
      values = [local.app_fqdn]
    }
  }

  condition {
    path_pattern {
      values = [
        "/engagements*",
        "/connections*",
      ]
    }
  }

  action {
    type = "forward"

    forward {
      target_group {
        arn    = aws_lb_target_group.server.arn
        weight = 1
      }
    }
  }
}

resource "aws_lb_listener_rule" "backend_api_pipeline" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 110

  condition {
    host_header {
      values = [local.app_fqdn]
    }
  }

  condition {
    path_pattern {
      values = [
        "/pipeline*",
        "/copilotkit*",
      ]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.server.arn
  }
}
