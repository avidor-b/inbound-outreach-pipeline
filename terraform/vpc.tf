# Dedicated VPC for Outreach.
#
# Why not stay on the default VPC: we need two things the default
# VPC can't express cleanly —
#   (1) a private subnet (for RDS in Phase C and EC2 in Phase D)
#   (2) tight SG-to-SG ingress rules, which require consistent VPC ownership
#
# Topology:
#   VPC 10.0.0.0/16
#     ├─ public-a   10.0.0.0/24   (ALB, NAT, bastion)
#     ├─ public-b   10.0.1.0/24   (ALB second AZ — required)
#     ├─ private-a  10.0.10.0/24  (EC2 in Phase D, RDS in Phase C)
#     └─ private-b  10.0.11.0/24  (RDS replica AZ — required for subnet group)
#
# NAT gateway lives in public-a. Private subnets route egress through it
# so EC2 can still reach ECR / SSM / LLM APIs after being moved out of
# the public subnet in Phase D.

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "outreach" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-vpc"
  })
}

# --- Subnets --------------------------------------------------------------

resource "aws_subnet" "public" {
  for_each = {
    a = { cidr = "10.0.0.0/24", az_idx = 0 }
    b = { cidr = "10.0.1.0/24", az_idx = 1 }
  }

  vpc_id                  = aws_vpc.outreach.id
  cidr_block              = each.value.cidr
  availability_zone       = data.aws_availability_zones.available.names[each.value.az_idx]
  map_public_ip_on_launch = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-public-${each.key}"
    Tier = "public"
  })
}

resource "aws_subnet" "private" {
  for_each = {
    a = { cidr = "10.0.10.0/24", az_idx = 0 }
    b = { cidr = "10.0.11.0/24", az_idx = 1 }
  }

  vpc_id            = aws_vpc.outreach.id
  cidr_block        = each.value.cidr
  availability_zone = data.aws_availability_zones.available.names[each.value.az_idx]

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-private-${each.key}"
    Tier = "private"
  })
}

# --- Internet gateway (public egress) -------------------------------------

resource "aws_internet_gateway" "outreach" {
  vpc_id = aws_vpc.outreach.id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-igw"
  })
}

# --- NAT gateway (private egress) -----------------------------------------
# One NAT in public-a serves both private subnets. Multi-AZ NAT is
# available but doubles the bill ($65/mo → $130/mo). For a v0 single-
# tenant system the single-NAT availability risk is acceptable.

resource "aws_eip" "nat" {
  domain = "vpc"
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-nat-eip"
  })
}

resource "aws_nat_gateway" "outreach" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public["a"].id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-nat"
  })

  depends_on = [aws_internet_gateway.outreach]
}

# --- Route tables ---------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.outreach.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.outreach.id
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rt-public"
  })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.outreach.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.outreach.id
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-rt-private"
  })
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}

# --- VPC flow logs --------------------------------------------------------
# Captures all accept/reject traffic metadata for forensics. Goes to a
# dedicated log group; 30-day retention.

resource "aws_cloudwatch_log_group" "vpc_flow" {
  name              = "/outreach/${var.environment}/vpc-flow-logs"
  retention_in_days = 30
  tags              = local.common_tags
}

resource "aws_iam_role" "vpc_flow" {
  name_prefix = "${local.name_prefix}-flow-"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "vpc_flow" {
  name_prefix = "${local.name_prefix}-flow-"
  role        = aws_iam_role.vpc_flow.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_flow_log" "outreach" {
  log_destination      = aws_cloudwatch_log_group.vpc_flow.arn
  log_destination_type = "cloud-watch-logs"
  iam_role_arn         = aws_iam_role.vpc_flow.arn
  vpc_id               = aws_vpc.outreach.id
  traffic_type         = "REJECT"
  # Log only REJECTs to keep volume (and cost) low. Flip to ALL if you
  # need deeper forensics — CloudWatch ingest is $0.50/GB.

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-flow-logs"
  })
}
