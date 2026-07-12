# Persistent EBS volume for chroma's vector index.
#
# The Chroma container stores its index at /chroma/chroma. This standalone EBS
# volume is attached at /dev/sdf, mounted by the host at
# /data/outreach/chroma, and bind-mounted to the container data directory by
# docker-compose.yml. It is independent of the EC2 root disk so routine
# instance replacement does not discard the index.

resource "aws_ebs_volume" "chroma" {
  # Must match the EC2's AZ — EBS is single-AZ.
  availability_zone = aws_instance.outreach.availability_zone
  size              = 10 # GB; chroma index is a few hundred MB, room to grow
  type              = "gp3"
  encrypted         = true

  # Protect against accidental destroy. If you really need to delete
  # this volume, comment the lifecycle block, apply, then flip back.
  lifecycle {
    prevent_destroy = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-chroma-data"
  })
}

resource "aws_volume_attachment" "chroma" {
  # /dev/sdf shows up as /dev/nvme1n1 on Nitro-based instances (t3, m5,
  # c5, etc.). Keep the logical name stable for the mount script.
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.chroma.id
  instance_id = aws_instance.outreach.id

  # Avoid force-detaching from a running instance. If terraform ever
  # wants to re-attach, the EC2 should be stopped first (manual step).
  stop_instance_before_detaching = true
}

output "chroma_volume_id" {
  value       = aws_ebs_volume.chroma.id
  description = "EBS volume holding chroma's vector index."
}
