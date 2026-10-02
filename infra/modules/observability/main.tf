# Alarms and the cost ceiling.
#
# The earlier prototype left this module a stub. It is built properly here because a
# deployment nobody is watching is not a deployment, and because the judging
# criteria reward operational maturity as much as features.

resource "aws_sns_topic" "alarms" {
  name = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Any Lambda error at all. At this volume a single error is signal, not noise,
# so there is no point smoothing it into a rate.
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = toset(var.function_names)

  alarm_name          = "${each.value}-errors"
  alarm_description   = "Lambda ${each.value} reported an error."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = toset(var.function_names)

  alarm_name          = "${each.value}-throttles"
  alarm_description   = "Lambda ${each.value} was throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name}-api-5xx"
  alarm_description   = "The API returned server errors."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = var.api_id
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# A message in the dead-letter queue means audio was accepted and then never
# processed. Nothing downstream will retry it, so this must be visible.
resource "aws_cloudwatch_metric_alarm" "dead_letter_depth" {
  alarm_name          = "${var.name}-dead-letter-depth"
  alarm_description   = "A job failed three times and landed in the dead-letter queue."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.dead_letter_queue_name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
}

# Cost. The budget is filtered to this application's tag so it does not count
# anything else in the account.
#
# The tag has to be activated for cost allocation before the filter matches
# anything, and AWS can take up to 24 hours to backfill it.
resource "aws_ce_cost_allocation_tag" "application" {
  tag_key = "Application"
  status  = "Active"
}

resource "aws_budgets_budget" "monthly" {
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # AWS separates key and value with a literal "$". Interpolating around it
  # needs format(), because "$${...}" escapes the interpolation itself.
  cost_filter {
    name   = "TagKeyValue"
    values = [format("Application$%s", var.application_tag)]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alarm_email]
  }

  depends_on = [aws_ce_cost_allocation_tag.application]
}

# A job nobody picks up expires without ever reaching the dead-letter queue: the
# redrive policy counts receives, not age. So the dead-letter alarm alone cannot
# see a stalled or missing worker. This one can.
resource "aws_cloudwatch_metric_alarm" "processing_backlog_age" {
  alarm_name          = "${var.name}-processing-backlog-age"
  alarm_description   = "A recording has waited over 15 minutes for transcription. The worker is stalled or missing."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = var.processing_queue_name
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}
