# CloudWatch: log shipping + alarms + SNS notification.
#
# Memory + disk metrics aren't collected by default on EC2 — the
# CloudWatch agent has to be installed and configured (user_data.sh
# handles that). The config it reads is in /opt/outreach/cwagent.json,
# templated from terraform.cloudwatch_agent_config below.
#
# Alarm thresholds are conservative defaults — tune after a week of
# baseline data.

# --- Log groups -----------------------------------------------------------
# 60-day retention is a balance between "enough to debug an incident"
# and "not burning money on logs no one reads". Extend per-group if you
# need more.

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/outreach/${var.environment}/cwagent"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_cloudwatch_log_group" "docker" {
  name              = "/outreach/${var.environment}/docker"
  retention_in_days = 60
  tags              = local.common_tags
}

# --- SNS topic for alarms -------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name_prefix = "${local.name_prefix}-alerts-"
  tags        = local.common_tags
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Metric alarms --------------------------------------------------------
# Memory + disk come from the CloudWatch agent (metrics namespace
# "CWAgent"). CPU + status-check come from the default EC2 namespace.

resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  alarm_name          = "${local.name_prefix}-high-cpu"
  alarm_description   = "EC2 CPU > 90% for 15 minutes"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 15
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.outreach.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "high_memory" {
  alarm_name          = "${local.name_prefix}-high-memory"
  alarm_description   = "EC2 memory > 85% for 10 minutes — may be approaching OOM"
  namespace           = "CWAgent"
  metric_name         = "mem_used_percent"
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 10
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.outreach.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  treat_missing_data  = "notBreaching"
  tags                = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "high_disk" {
  alarm_name          = "${local.name_prefix}-high-disk"
  alarm_description   = "Root disk > 85% used"
  namespace           = "CWAgent"
  metric_name         = "disk_used_percent"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 85
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    InstanceId = aws_instance.outreach.id
    path       = "/"
    fstype     = "xfs"
  }
  alarm_actions      = [aws_sns_topic.alerts.arn]
  ok_actions         = [aws_sns_topic.alerts.arn]
  treat_missing_data = "notBreaching"
  tags               = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "instance_unhealthy" {
  alarm_name          = "${local.name_prefix}-instance-unhealthy"
  alarm_description   = "EC2 failed a system status check"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { InstanceId = aws_instance.outreach.id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
  tags                = local.common_tags
}

# --- CloudWatch agent config ---------------------------------------------
# Pushed to /opt/outreach/cwagent.json by user_data.sh; the agent starts
# as a systemd unit.

locals {
  cloudwatch_agent_config = jsonencode({
    agent = {
      metrics_collection_interval = 60
      run_as_user                 = "root"
    }
    metrics = {
      namespace = "CWAgent"
      append_dimensions = {
        InstanceId = "$${aws:InstanceId}"
      }
      metrics_collected = {
        mem    = { measurement = ["mem_used_percent"] }
        swap   = { measurement = ["swap_used_percent"] }
        disk   = { measurement = ["used_percent"], resources = ["/"] }
        diskio = { measurement = ["io_time"], resources = ["*"] }
      }
    }
    logs = {
      logs_collected = {
        files = {
          collect_list = [
            {
              file_path       = "/var/lib/docker/containers/*/*.log"
              log_group_name  = aws_cloudwatch_log_group.docker.name
              log_stream_name = "{instance_id}-{hostname}"
              timezone        = "UTC"
            },
          ]
        }
      }
    }
  })
}

# Ship the config to SSM Parameter Store so the agent on the box can
# fetch it by name. This also means rotating the config is a one-liner
# `aws ssm put-parameter --overwrite ...` with no redeploy.
resource "aws_ssm_parameter" "cwagent_config" {
  name  = "/outreach/${var.environment}/cwagent-config"
  type  = "String"
  value = local.cloudwatch_agent_config
  tags  = local.common_tags
}
