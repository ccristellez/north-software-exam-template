# Terraform Infrastructure

This folder contains **Infrastructure as Code** (IaC) for deploying the Congestion Monitor to AWS. If you're new to Terraform, this README will explain everything step by step.

---

## What is Terraform?

Terraform is a tool that lets you define cloud infrastructure (servers, databases, networks) as code. Instead of clicking through the AWS console, you write configuration files and Terraform creates/updates/deletes resources for you.

**Benefits:**
- **Reproducible** - Run the same code to create identical environments
- **Version controlled** - Track changes in Git like any other code
- **Self-documenting** - The code shows exactly what infrastructure exists

---

## What This Terraform Creates

When you run `terraform apply`, it creates these AWS resources:

```
┌─────────────────────────────────────────────────────────────────────┐
│                              AWS Cloud                               │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                         VPC (Network)                        │   │
│  │                                                              │   │
│  │   ┌──────────────────┐      ┌──────────────────────────┐   │   │
│  │   │  API Gateway     │      │    Private Subnets       │   │   │
│  │   │  (HTTP endpoint) │─────▶│                          │   │   │
│  │   │                  │      │  ┌─────────────────────┐ │   │   │
│  │   │  Rate limiting   │      │  │   Lambda Function   │ │   │   │
│  │   │  CORS headers    │      │  │   (Your FastAPI)    │ │   │   │
│  │   └──────────────────┘      │  │                     │ │   │   │
│  │                             │  │  Runs your Python   │ │   │   │
│  │                             │  │  code on demand     │ │   │   │
│  │                             │  └──────────┬──────────┘ │   │   │
│  │                             │             │            │   │   │
│  │                             │  ┌──────────▼──────────┐ │   │   │
│  │                             │  │   ElastiCache       │ │   │   │
│  │                             │  │   (Managed Redis)   │ │   │   │
│  │                             │  │                     │ │   │   │
│  │                             │  │  Stores real-time   │ │   │   │
│  │                             │  │  device counts      │ │   │   │
│  │                             │  └─────────────────────┘ │   │   │
│  │                             └──────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  + CloudWatch (logs & monitoring)                                   │
│  + SNS (alert notifications)                                        │
└─────────────────────────────────────────────────────────────────────┘
```

**Note:** This doesn't create the PostgreSQL database - we use Supabase for that (it's a separate managed service, not part of this Terraform).

---

## Key Concepts Explained

### Lambda (Serverless Compute)
- **What:** Runs your Python code without managing servers
- **How:** AWS automatically starts your code when a request comes in
- **Cost:** You only pay when code is running (free tier = 1M requests/month)
- **Cold start:** First request after idle may take 100-500ms longer

### API Gateway (HTTP Endpoint)
- **What:** Gives your Lambda a public URL that anyone can call
- **How:** Routes HTTP requests to Lambda, handles CORS, rate limiting
- **Cost:** ~$3.50 per million requests

### ElastiCache (Managed Redis)
- **What:** AWS-managed Redis (same as running `docker-compose up redis`)
- **Why:** No server to manage, automatic backups, multi-AZ failover
- **Cost:** ~$12/month (smallest) to ~$100/month (production)

### VPC (Virtual Private Cloud)
- **What:** Your own isolated network in AWS
- **Why:** Security - Redis is only accessible from your Lambda, not the internet
- **Subnets:** Public (can reach internet) and Private (isolated, more secure)

### NAT Gateway
- **What:** Allows Lambda in private subnet to reach the internet
- **Why needed:** Only if Lambda needs to call external APIs (like Supabase)
- **Cost:** ~$32/month (disabled by default to save money)

---

## File Structure Explained

```
terraform/
├── main.tf              # Main configuration - connects all the pieces
├── variables.tf         # Input variables (like function parameters)
├── outputs.tf           # Output values (like return values)
├── modules/             # Reusable components
│   ├── vpc/             # Network setup (VPC, subnets, routing)
│   ├── lambda/          # Lambda + API Gateway
│   └── elasticache/     # Redis cluster
└── environments/        # Environment-specific settings
    ├── dev/             # Development (cheap, minimal)
    └── prod/            # Production (reliable, multi-AZ)
```

### How Modules Work

Think of modules like functions in programming:

```hcl
# main.tf calls the vpc module with parameters
module "vpc" {
  source = "./modules/vpc"        # Where the module code lives

  project_name = "congestion-monitor"  # Input: project name
  environment  = "dev"                  # Input: environment
  az_count     = 2                      # Input: how many availability zones
}

# The module creates resources and returns values
# Example: module.vpc.vpc_id, module.vpc.private_subnet_ids
```

---

## How to Deploy (Step by Step)

### Prerequisites

1. **Install Terraform** (version 1.0 or higher)
   ```bash
   # macOS
   brew install terraform

   # Windows (with Chocolatey)
   choco install terraform

   # Or download from https://terraform.io/downloads
   ```

2. **Install AWS CLI** and configure credentials
   ```bash
   # Install AWS CLI
   brew install awscli  # macOS

   # Configure with your AWS credentials
   aws configure
   # Enter your Access Key ID, Secret Access Key, region (us-east-1)
   ```

3. **Build the Lambda package** (your Python code as a .zip file)
   ```bash
   # From the project root directory
   pip install -r requirements.txt -t dist/package/
   cp -r src dist/package/
   cd dist/package && zip -r ../lambda.zip . && cd ../..
   ```

### Deploy to Development

```bash
# 1. Go to terraform folder
cd terraform

# 2. Initialize Terraform (downloads AWS provider)
terraform init

# 3. Preview what will be created (no changes made yet)
terraform plan -var-file=environments/dev/terraform.tfvars

# 4. Create the infrastructure (type "yes" to confirm)
terraform apply -var-file=environments/dev/terraform.tfvars

# 5. Note the output URL - that's your API endpoint!
# Example: api_endpoint = "https://abc123.execute-api.us-east-1.amazonaws.com"
```

### Test Your Deployment

```bash
# Replace URL with your actual api_endpoint output
API_URL="https://abc123.execute-api.us-east-1.amazonaws.com"

# Health check
curl $API_URL/health

# Send a ping
curl -X POST $API_URL/v1/pings \
  -H "Content-Type: application/json" \
  -d '{"device_id":"test1","lat":40.743,"lon":-73.989,"speed_kmh":45}'

# Query congestion
curl "$API_URL/v1/congestion?lat=40.743&lon=-73.989"
```

### Destroy (Delete Everything)

```bash
# This deletes ALL resources created by Terraform
terraform destroy -var-file=environments/dev/terraform.tfvars
```

---

## Environments Explained

### Development (`environments/dev/terraform.tfvars`)

Optimized for **low cost** while learning/testing:

| Setting | Value | Why |
|---------|-------|-----|
| `az_count` | 2 | Minimum for basic redundancy |
| `enable_nat_gateway` | false | Saves $32/month |
| `redis_node_type` | cache.t3.micro | Smallest/cheapest (~$12/month) |
| `redis_num_cache_clusters` | 1 | No replication (single node) |
| `lambda_provisioned_concurrency` | 0 | Accept cold starts |
| `enable_alerts` | false | No email notifications |

**Estimated cost: ~$15-25/month**

### Production (`environments/prod/terraform.tfvars`)

Optimized for **reliability and performance**:

| Setting | Value | Why |
|---------|-------|-----|
| `az_count` | 3 | High availability across 3 zones |
| `enable_nat_gateway` | true | Lambda can reach external APIs |
| `redis_node_type` | cache.r6g.large | Good performance (~$100/month) |
| `redis_num_cache_clusters` | 2 | Primary + replica for failover |
| `lambda_provisioned_concurrency` | 5 | Reduce cold starts |
| `enable_alerts` | true | Get notified of problems |

**Estimated cost: ~$150-250/month**

---

## Common Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `environment` | Name like "dev" or "prod" | **required** |
| `aws_region` | AWS region to deploy | us-east-1 |
| `vpc_cidr` | IP address range for VPC | 10.0.0.0/16 |
| `lambda_memory_size` | Memory for Lambda (MB) | 256 |
| `lambda_timeout` | Max execution time (seconds) | 30 |
| `redis_node_type` | ElastiCache instance size | cache.t3.micro |
| `enable_nat_gateway` | Allow Lambda internet access | false |
| `enable_alerts` | CloudWatch alarms + email | false |

---

## Troubleshooting

### "Error: No valid credential sources found"
You need to configure AWS credentials:
```bash
aws configure
# Or set environment variables:
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
```

### "Error: Error creating Lambda function: InvalidParameterValueException"
The Lambda .zip package doesn't exist. Build it first:
```bash
cd ..  # Go to project root
pip install -r requirements.txt -t dist/package/
cp -r src dist/package/
cd dist/package && zip -r ../lambda.zip . && cd ../..
```

### "terraform plan" shows no changes but resources exist
You might be using a different state file. Make sure you're in the right directory and using the correct `-var-file`.

### Lambda is timing out
1. Check CloudWatch logs: AWS Console → CloudWatch → Log Groups → `/aws/lambda/congestion-monitor-api`
2. Increase `lambda_timeout` in your tfvars file
3. Check if Redis connection is failing (security group issue)

### Cold starts are too slow
Enable provisioned concurrency (costs ~$0.015/hour per instance):
```hcl
lambda_provisioned_concurrency = 2  # Keep 2 instances warm
```

---

## Cost Breakdown

| Component | Dev | Prod | Notes |
|-----------|-----|------|-------|
| **Lambda** | ~$0 | ~$10-50 | Free tier covers 1M requests |
| **API Gateway** | ~$1 | ~$10 | $3.50/million requests |
| **ElastiCache** | ~$12 | ~$100 | Biggest cost - instance size |
| **NAT Gateway** | $0 | ~$32 | Only if enabled |
| **CloudWatch** | ~$5 | ~$10 | Logs and metrics |
| **Total** | **~$15-25** | **~$150-250** | Per month |

---

## What's NOT Included

This Terraform does **not** create:

1. **Supabase PostgreSQL** - We use Supabase's managed database for historical data (sign up at supabase.com, it's free for small projects)
2. **Domain name / SSL** - Add Route53 + ACM if you want a custom domain
3. **CI/CD pipeline** - Could add GitHub Actions to auto-deploy on push

---

## Next Steps

1. **Deploy dev environment** - Follow the steps above
2. **Set up Supabase** - Create database and run the SQL from `docs/schema.sql`
3. **Add DATABASE_URL** - Add Supabase connection string to Lambda environment variables
4. **Test the API** - Send pings and query congestion
5. **Monitor** - Check CloudWatch for logs and metrics
6. **Production** - When ready, deploy with `environments/prod/terraform.tfvars`

---

## Further Reading

- [Terraform AWS Provider Docs](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS Lambda Developer Guide](https://docs.aws.amazon.com/lambda/latest/dg/welcome.html)
- [ElastiCache for Redis Guide](https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/WhatIs.html)
