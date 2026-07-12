# Copy to backend.hcl and replace every value for your own AWS account, or run
# bootstrap_backend.sh to generate it, then initialize Terraform:
#   terraform init -backend-config=backend.hcl
#
# The bucket and DynamoDB table must already exist in the AWS account
# that owns this environment. Do not point new accounts at another
# account's backend.
bucket         = "replace-with-your-terraform-state-bucket"
key            = "outreach-pipeline/prod/terraform.tfstate"
region         = "us-east-1"
encrypt        = true
dynamodb_table = "replace-with-your-terraform-lock-table"
