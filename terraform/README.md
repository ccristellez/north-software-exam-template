# Terraform Infrastructure

AWS infrastructure for the Congestion Monitor API using serverless architecture.

## Architecture

- **Lambda** - FastAPI application with Mangum adapter
- **API Gateway** - HTTP API for routing and throttling
- **ElastiCache** - Managed Redis for data storage
- **VPC** - Network isolation with public/private subnets
- **CloudWatch** - Logging and alarms

## Prerequisites

1. AWS CLI configured with credentials
2. Terraform >= 1.0 installed
3. Lambda deployment package built (see below)

## Quick Start

```bash
# Build Lambda package first
cd ..
pip install -r requirements.txt -t dist/package/
cp -r src dist/package/
cd dist/package && zip -r ../lambda.zip . && cd ../..

# Initialize Terraform
cd terraform
terraform init

# Plan (dev environment)
terraform plan -var-file=environments/dev/terraform.tfvars

# Apply (requires AWS credentials)
terraform apply -var-file=environments/dev/terraform.tfvars
```

## Environments

### Development (`environments/dev/`)
- Minimal resources for cost savings
- Single Redis node (no replication)
- No NAT Gateway (~$32/month savings)
- No provisioned concurrency (cold starts acceptable)
- **Estimated cost: ~$15-25/month**

### Production (`environments/prod/`)
- Multi-AZ deployment (3 AZs)
- Redis replication with automatic failover
- Provisioned concurrency for low latency
- CloudWatch alarms enabled
- **Estimated cost: ~$150-250/month**

## Module Structure

```
terraform/
├── main.tf              # Root module, composes all modules
├── variables.tf         # Input variables
├── outputs.tf           # Output values
├── modules/
│   ├── vpc/            # VPC, subnets, routing
│   ├── elasticache/    # Redis cluster
│   └── lambda/         # Lambda + API Gateway
└── environments/
    ├── dev/            # Dev environment config
    └── prod/           # Prod environment config
```

## Key Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `environment` | Environment name | required |
| `aws_region` | AWS region | us-east-1 |
| `redis_node_type` | ElastiCache instance type | cache.t3.micro |
| `redis_num_cache_clusters` | Number of Redis nodes | 1 |
| `lambda_memory_size` | Lambda memory (MB) | 256 |
| `lambda_provisioned_concurrency` | Warm instances | 0 |
| `enable_nat_gateway` | Enable NAT (~$32/month) | false |
| `enable_alerts` | Enable CloudWatch alarms | false |

## Outputs

After applying, Terraform outputs:
- `api_endpoint` - API Gateway URL for requests
- `lambda_function_name` - Lambda function name
- `redis_endpoint` - Redis connection string (sensitive)

## Cost Breakdown

| Component | Dev | Prod |
|-----------|-----|------|
| Lambda | ~$0 (free tier) | ~$10-50 |
| API Gateway | ~$3/million req | ~$3/million req |
| ElastiCache | ~$12 (t3.micro) | ~$100 (r6g.large) |
| NAT Gateway | $0 (disabled) | ~$32 |
| CloudWatch | ~$5 | ~$10-20 |
| **Total** | **~$15-25/month** | **~$150-250/month** |

## Destroying Infrastructure

```bash
terraform destroy -var-file=environments/dev/terraform.tfvars
```

## Notes

- Lambda requires VPC access to reach ElastiCache
- NAT Gateway is only needed if Lambda needs internet access (external APIs)
- For this application, VPC endpoints + ElastiCache are sufficient without NAT
- Provisioned concurrency costs ~$0.015/hour per instance
