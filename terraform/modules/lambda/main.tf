# Lambda Module - Serverless API for Congestion Monitor
# Deploys FastAPI application with Mangum adapter behind API Gateway

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# IAM Role for Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "${var.project_name}-lambda-role"
    Environment = var.environment
  }
}

# Basic Lambda execution (CloudWatch Logs)
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# VPC access for Lambda
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Custom policy for application-specific permissions
resource "aws_iam_role_policy" "lambda_custom" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Allow ElastiCache access (for Redis)
        Effect = "Allow"
        Action = [
          "elasticache:DescribeCacheClusters",
          "elasticache:DescribeReplicationGroups"
        ]
        Resource = "*"
      },
      {
        # Allow X-Ray tracing (optional)
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords"
        ]
        Resource = "*"
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# Security Group for Lambda
# -----------------------------------------------------------------------------

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg"
  description = "Security group for Lambda function"
  vpc_id      = var.vpc_id

  # Allow outbound to Redis
  egress {
    description = "Redis access"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  # Allow outbound HTTPS (for AWS services)
  egress {
    description = "HTTPS for AWS services"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-lambda-sg"
    Environment = var.environment
  }
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "api" {
  function_name = "${var.project_name}-api"
  description   = "Congestion Monitor API"

  # Deployment package (placeholder - would be built in CI/CD)
  filename         = var.lambda_zip_path
  source_code_hash = var.lambda_source_hash
  handler          = "src.api.lambda_handler.handler"
  runtime          = "python3.12"

  # Resources
  memory_size = var.memory_size
  timeout     = var.timeout

  # IAM role
  role = aws_iam_role.lambda.arn

  # VPC configuration (to access ElastiCache)
  vpc_config {
    subnet_ids         = var.private_subnet_ids
    security_group_ids = [aws_security_group.lambda.id]
  }

  # Environment variables
  environment {
    variables = {
      ENVIRONMENT = var.environment
      REDIS_HOST  = var.redis_endpoint
      REDIS_PORT  = var.redis_port
      LOG_LEVEL   = var.environment == "prod" ? "INFO" : "DEBUG"
    }
  }

  # Tracing (X-Ray)
  tracing_config {
    mode = var.environment == "prod" ? "Active" : "PassThrough"
  }

  # Reserved concurrency (optional - prevents runaway costs)
  reserved_concurrent_executions = var.reserved_concurrency

  tags = {
    Name        = "${var.project_name}-api"
    Environment = var.environment
    Project     = var.project_name
  }
}

# -----------------------------------------------------------------------------
# Lambda Provisioned Concurrency (optional - reduces cold starts)
# -----------------------------------------------------------------------------

resource "aws_lambda_alias" "live" {
  name             = "live"
  description      = "Live alias for production traffic"
  function_name    = aws_lambda_function.api.function_name
  function_version = aws_lambda_function.api.version
}

resource "aws_lambda_provisioned_concurrency_config" "api" {
  count = var.provisioned_concurrency > 0 ? 1 : 0

  function_name                     = aws_lambda_function.api.function_name
  qualifier                         = aws_lambda_alias.live.name
  provisioned_concurrent_executions = var.provisioned_concurrency
}

# -----------------------------------------------------------------------------
# API Gateway (HTTP API - simpler and cheaper than REST API)
# -----------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = "${var.project_name}-api"
  description   = "Congestion Monitor HTTP API"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = var.cors_origins
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 300
  }

  tags = {
    Name        = "${var.project_name}-api"
    Environment = var.environment
  }
}

# Lambda integration
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

# Default route (catch-all)
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Stage (auto-deploy)
resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      latency        = "$context.responseLatency"
    })
  }

  default_route_settings {
    throttling_burst_limit = var.api_throttle_burst
    throttling_rate_limit  = var.api_throttle_rate
  }

  tags = {
    Environment = var.environment
  }
}

# Permission for API Gateway to invoke Lambda
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# -----------------------------------------------------------------------------
# CloudWatch Logs
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${aws_lambda_function.api.function_name}"
  retention_in_days = var.environment == "prod" ? 30 : 7

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_log_group" "api_gateway" {
  name              = "/aws/apigateway/${var.project_name}"
  retention_in_days = var.environment == "prod" ? 30 : 7

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project_name}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "Lambda function error rate is high"

  dimensions = {
    FunctionName = aws_lambda_function.api.function_name
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_duration" {
  alarm_name          = "${var.project_name}-lambda-duration"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Duration"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Average"
  threshold           = var.timeout * 0.8 * 1000  # 80% of timeout in ms
  alarm_description   = "Lambda function duration is approaching timeout"

  dimensions = {
    FunctionName = aws_lambda_function.api.function_name
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${var.project_name}-lambda-throttles"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "Lambda function is being throttled"

  dimensions = {
    FunctionName = aws_lambda_function.api.function_name
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.project_name}-api-5xx-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "5XXError"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 10
  alarm_description   = "API Gateway 5XX error rate is high"

  dimensions = {
    ApiId = aws_apigatewayv2_api.main.id
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  alarm_name          = "${var.project_name}-api-latency"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Latency"
  namespace           = "AWS/ApiGateway"
  period              = 300
  statistic           = "p99"
  threshold           = 1000  # 1 second p99
  alarm_description   = "API Gateway p99 latency is high"

  dimensions = {
    ApiId = aws_apigatewayv2_api.main.id
  }

  alarm_actions = var.sns_topic_arn != null ? [var.sns_topic_arn] : []

  tags = {
    Environment = var.environment
    Project     = var.project_name
  }
}
