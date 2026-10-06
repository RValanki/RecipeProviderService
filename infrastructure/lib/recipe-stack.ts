import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cw_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sns_subs from 'aws-cdk-lib/aws-sns-subscriptions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as path from 'path';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import { Construct } from 'constructs';

/// Fail the synth if a required secret is missing, rather than deploying an
/// empty string and silently breaking the Lambda at runtime.
function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `Missing required env var ${name}. Add it to RecipeProviderService/.env (auto-loaded) before running cdk deploy.`
    );
  }
  return value;
}

export class RecipeStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // -----------------------------
    // yt-dlp Layer
    // -----------------------------
    const ytDlpLayer = new lambda.LayerVersion(this, 'YtDlpLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../../layers/yt-dlp')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'yt-dlp binary layer',
    });

    // -----------------------------
    // ffmpeg Layer
    // -----------------------------
    const ffmpegLayer = new lambda.LayerVersion(this, 'FfmpegLayer', {
      code: lambda.Code.fromAsset(path.join(__dirname, '../../layers/ffmpeg')),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: 'ffmpeg binary layer',
    });

    // -----------------------------
    // Instagram Cookies S3 Bucket (pre-existing, created manually)
    // -----------------------------
    const instagramCookiesBucket = s3.Bucket.fromBucketName(
      this,
      'InstagramCookiesBucket',
      'recipe-instagram-cookies'
    );

    // -----------------------------
    // TikTokMediaProcessor Lambda
    // -----------------------------
    const tikTokMediaProcessor = new lambda.Function(this, 'TikTokMediaProcessor', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'TikTokMediaHandler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../recipe-processing-lambda'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install -r handlers/requirements.txt -t /asset-output && cp -r handlers/. /asset-output && cp -r service/. /asset-output',
          ],
        },
      }),
      layers: [ytDlpLayer, ffmpegLayer],
      timeout: cdk.Duration.minutes(5),
      memorySize: 2048,
      environment: {
        OPENAI_API_KEY: requireEnv('OPENAI_API_KEY'),
      },
    });

    // -----------------------------
    // InstagramMediaProcessor Lambda
    // -----------------------------
    const instagramMediaProcessor = new lambda.Function(this, 'InstagramMediaProcessor', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'InstagramMediaHandler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../recipe-processing-lambda'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install -r handlers/requirements.txt -t /asset-output && cp -r handlers/. /asset-output && cp -r service/. /asset-output',
          ],
        },
      }),
      layers: [ytDlpLayer, ffmpegLayer],
      timeout: cdk.Duration.minutes(5),
      memorySize: 2048,
      environment: {
        OPENAI_API_KEY: requireEnv('OPENAI_API_KEY'),
        COOKIES_BUCKET: 'recipe-instagram-cookies',
        COOKIES_KEY: 'cookies/instagram_cookies.txt',
      },
    });

    // Grant InstagramMediaProcessor read access to the cookies bucket
    instagramCookiesBucket.grantRead(instagramMediaProcessor);

    // -----------------------------
    // RecipeProcessor Lambda
    // -----------------------------
    const recipeProcessor = new lambda.Function(this, 'RecipeProcessor', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../recipe-processing-lambda'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install -r handlers/requirements.txt -t /asset-output && cp -r handlers/. /asset-output && cp -r service/. /asset-output',
          ],
        },
      }),
      timeout: cdk.Duration.seconds(150),   // 2:30 worst-case import
      // Lambda CPU scales with memory; 512 MB was CPU-starved, slowing JSON/HTML
      // parsing and every OpenAI TLS round-trip. 1536 MB (~1 vCPU) cuts wall-time
      // on every import — and since faster = shorter billed duration, near cost-neutral.
      memorySize: 1536,
      // NOTE: the concurrency cap is applied on the SQS event source below
      // (maxConcurrency), NOT as reservedConcurrentExecutions. Reserving capacity
      // requires the account to keep >=10 unreserved; on a low-limit account that
      // reservation is rejected. maxConcurrency caps the worker without reserving.
      environment: {
        OPENAI_API_KEY: requireEnv('OPENAI_API_KEY'),
        MEDIA_LAMBDA_NAME: tikTokMediaProcessor.functionName,
        INSTAGRAM_MEDIA_LAMBDA_NAME: instagramMediaProcessor.functionName,
        FIREBASE_SERVICE_ACCOUNT: requireEnv('FIREBASE_SERVICE_ACCOUNT'),
        // Target bucket for re-hosted recipe thumbnails. Empty → ImageRehost falls
        // back to the project's default bucket ({project_id}.appspot.com). Set this
        // in .env if your Firebase Storage bucket differs (e.g. *.firebasestorage.app).
        STORAGE_BUCKET: process.env.STORAGE_BUCKET ?? '',
      },
    });

    // -----------------------------
    // Grant RecipeProcessor permission to invoke TikTokMediaProcessor
    // -----------------------------
    tikTokMediaProcessor.grantInvoke(recipeProcessor);

    // -----------------------------
    // Grant RecipeProcessor permission to invoke InstagramMediaProcessor
    // -----------------------------
    instagramMediaProcessor.grantInvoke(recipeProcessor);

    // -----------------------------
    // Recipe import queue (+ DLQ)
    // -----------------------------
    // Imports run far longer than API Gateway's 29s timeout, so the request is
    // enqueued and processed asynchronously. SQS also absorbs spikes — a burst of
    // imports queues up instead of melting Lambda/OpenAI.
    const recipeImportDLQ = new sqs.Queue(this, 'RecipeImportDLQ', {
      queueName: 'recipe-import-dlq',
      retentionPeriod: cdk.Duration.days(14),  // keep failures around to inspect
      encryption: sqs.QueueEncryption.SQS_MANAGED,
    });

    const recipeImportQueue = new sqs.Queue(this, 'RecipeImportQueue', {
      queueName: 'recipe-import-queue',
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      // Just above the worker timeout (150s) so a running job isn't re-delivered,
      // with a little buffer. Kept short (not 6x) because we do NOT want a long
      // retry tail — the client gives up at ~135s, so churning retries for minutes
      // afterwards only wastes OpenAI spend and produces stale "zombie" successes.
      visibilityTimeout: cdk.Duration.seconds(180),
      deadLetterQueue: {
        queue: recipeImportDLQ,
        // One attempt only. The worker notifies the client of caught failures
        // itself (recipeFailed event) and returns cleanly; the DLQ is reserved for
        // the hard-timeout/crash case, handled by the failure consumer below.
        maxReceiveCount: 1,
      },
    });

    // -----------------------------
    // Per-user import quota table
    // -----------------------------
    // Atomic per-user counters for daily + monthly import limits (cost control /
    // abuse protection). One row per (uid, window); the window is encoded in the
    // key so a new day/month starts fresh. TTL just garbage-collects old rows.
    const quotaTable = new dynamodb.Table(this, 'RecipeImportQuota', {
      tableName: 'recipe-import-quota',
      partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: 'ttl',
      removalPolicy: cdk.RemovalPolicy.DESTROY, // just counters — safe to recreate
    });

    // -----------------------------
    // RecipeEnqueue Lambda (producer — thin, fast, sits behind the gateway)
    // -----------------------------
    // Authenticates (uid comes from the authorizer context, never the body),
    // validates the input, enforces per-user quota, drops the job on the queue,
    // returns 202 (or 429 when a limit is hit).
    const recipeEnqueue = new lambda.Function(this, 'RecipeEnqueue', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'enqueue_handler.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../recipe-processing-lambda'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install -r handlers/requirements.txt -t /asset-output && cp -r handlers/. /asset-output && cp -r service/. /asset-output',
          ],
        },
      }),
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      environment: {
        RECIPE_QUEUE_URL: recipeImportQueue.queueUrl,
        QUOTA_TABLE_NAME: quotaTable.tableName,
        DAILY_IMPORT_LIMIT: '30',
        MONTHLY_IMPORT_LIMIT: '150',
      },
    });

    // Producer may only send to the queue + read/write its own quota counters.
    recipeImportQueue.grantSendMessages(recipeEnqueue);
    quotaTable.grantReadWriteData(recipeEnqueue);

    // Let the API Gateway invoke the producer. The gateway imports this Lambda by
    // ARN (in CookiGatewayCDK), so it can't add this permission itself; granting
    // it here — scoped to any API in this account/region — avoids a cross-stack
    // circular dependency.
    recipeEnqueue.addPermission('AllowApiGatewayInvoke', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: `arn:aws:execute-api:${this.region}:${this.account}:*/*/*/*`,
    });

    // -----------------------------
    // Wire the worker to the queue
    // -----------------------------
    // batchSize 1 = one heavy import per invocation; failures fail just that
    // message so SQS retries it independently (and DLQs after maxReceiveCount).
    // maxConcurrency caps how many workers run at once (blast-radius control for
    // OpenAI / media Lambdas) WITHOUT reserving account concurrency. The account's
    // total Lambda concurrency is only ~10, and each import uses ~2 slots (worker +
    // the media Lambda it sync-invokes), so cap at the minimum of 2 to leave room
    // for the authorizer/enqueue/food Lambdas. Raise this after requesting a
    // Lambda concurrency quota increase (Service Quotas → "Concurrent executions").
    recipeProcessor.addEventSource(new SqsEventSource(recipeImportQueue, {
      batchSize: 1,
      maxConcurrency: 2,
    }));

    // -----------------------------
    // RecipeImportFailureHandler Lambda (DLQ consumer — fallback notifier)
    // -----------------------------
    // Fallback failure signal: when the worker is killed (hard timeout / crash)
    // before it can emit its own recipeFailed event, the job lands in the DLQ.
    // This Lambda drains the DLQ and writes recipeFailed so the client's pending
    // import resolves instead of hanging.
    const recipeImportFailureHandler = new lambda.Function(this, 'RecipeImportFailureHandler', {
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: 'handler.recipe_failure_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../recipe-processing-lambda'), {
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          platform: 'linux/amd64',
          command: [
            'bash', '-c',
            'pip install -r handlers/requirements.txt -t /asset-output && cp -r handlers/. /asset-output && cp -r service/. /asset-output',
          ],
        },
      }),
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        FIREBASE_SERVICE_ACCOUNT: requireEnv('FIREBASE_SERVICE_ACCOUNT'),
      },
    });

    recipeImportFailureHandler.addEventSource(new SqsEventSource(recipeImportDLQ, {
      batchSize: 1,
    }));

    // -----------------------------
    // Monitoring — alarms + dashboard for tuning import concurrency
    // -----------------------------
    // The decision logic these support:
    //   queue age/depth high + worker throttling  → under-provisioned, raise maxConcurrency
    //   queue age/depth high + OpenAI 429s         → OpenAI is the wall, raising workers won't help
    // Everything auto-scales down to ~0 when idle; these only fire under real load.
    const alertTopic = new sns.Topic(this, 'RecipeImportAlerts', {
      topicName: 'recipe-import-alerts',
    });
    // Swap for your real address (or wire to Slack/PagerDuty later).
    alertTopic.addSubscription(new sns_subs.EmailSubscription('rohitvalanki@gmail.com'));

    const notify = (alarm: cloudwatch.Alarm) =>
      alarm.addAlarmAction(new cw_actions.SnsAction(alertTopic));

    // === 1. THE tuning signal: how long the oldest job has waited ===
    // Approaching the client's ~135s budget means users are timing out and you
    // need MORE concurrency. Primary "scale up maxConcurrency" trigger.
    const queueAgeMetric = recipeImportQueue.metricApproximateAgeOfOldestMessage({
      period: cdk.Duration.minutes(1),
      statistic: 'Maximum',
    });
    notify(new cloudwatch.Alarm(this, 'ImportQueueAgeHigh', {
      alarmName: 'recipe-import-oldest-message-age',
      metric: queueAgeMetric,
      threshold: 60,                 // seconds — half the client budget
      evaluationPeriods: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    // === 2. Backlog building (secondary confirmation of under-provisioning) ===
    const queueDepthMetric = recipeImportQueue.metricApproximateNumberOfMessagesVisible({
      period: cdk.Duration.minutes(1),
      statistic: 'Maximum',
    });
    notify(new cloudwatch.Alarm(this, 'ImportQueueDepthHigh', {
      alarmName: 'recipe-import-queue-depth',
      metric: queueDepthMetric,
      threshold: 50,
      evaluationPeriods: 5,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    // === 3. You've HIT the ceiling — Lambda is throttling imports ===
    const workerThrottleMetric = recipeProcessor.metricThrottles({
      period: cdk.Duration.minutes(1),
      statistic: 'Sum',
    });
    notify(new cloudwatch.Alarm(this, 'WorkerThrottled', {
      alarmName: 'recipe-worker-throttles',
      metric: workerThrottleMetric,
      threshold: 1,
      evaluationPeriods: 3,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    // === 4. The OpenAI bottleneck — 429s in worker logs ===
    // If this trips while the queue is backed up, the bottleneck is OpenAI, not
    // Lambda — raising maxConcurrency won't help; raise your OpenAI tier instead.
    const openAiThrottleFilter = new logs.MetricFilter(this, 'OpenAiRateLimitFilter', {
      logGroup: recipeProcessor.logGroup,
      metricNamespace: 'Cooki/RecipeImport',
      metricName: 'OpenAiRateLimited',
      filterPattern: logs.FilterPattern.anyTerm('429', 'rate_limit', 'RateLimitError'),
      metricValue: '1',
    });
    const openAiThrottleMetric = openAiThrottleFilter.metric({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });
    notify(new cloudwatch.Alarm(this, 'OpenAiRateLimited', {
      alarmName: 'recipe-openai-rate-limited',
      metric: openAiThrottleMetric,
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    // === 5. Hard failures reaching the DLQ (crashes / hard timeouts) ===
    const dlqDepthMetric = recipeImportDLQ.metricApproximateNumberOfMessagesVisible({
      period: cdk.Duration.minutes(1),
      statistic: 'Maximum',
    });
    notify(new cloudwatch.Alarm(this, 'ImportDLQNotEmpty', {
      alarmName: 'recipe-import-dlq-not-empty',
      metric: dlqDepthMetric,
      threshold: 0,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    // === 6. Media-Lambda error rate (yt-dlp / cookie fragility) ===
    const mediaErrorMetrics: cloudwatch.IMetric[] = [];
    for (const [name, fn] of [
      ['TikTok', tikTokMediaProcessor],
      ['Instagram', instagramMediaProcessor],
    ] as const) {
      const errMetric = fn.metricErrors({
        period: cdk.Duration.minutes(5),
        statistic: 'Sum',
      });
      mediaErrorMetrics.push(errMetric);
      notify(new cloudwatch.Alarm(this, `${name}MediaErrors`, {
        alarmName: `recipe-${name.toLowerCase()}-media-errors`,
        metric: errMetric,
        threshold: 5,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      }));
    }

    // -----------------------------
    // Dashboard — watch queue-age vs concurrency vs OpenAI side by side
    // -----------------------------
    const workerConcurrencyMetric = recipeProcessor.metric('ConcurrentExecutions', {
      period: cdk.Duration.minutes(1),
      statistic: 'Maximum',
    });
    const dashboard = new cloudwatch.Dashboard(this, 'RecipeImportDashboard', {
      dashboardName: 'recipe-import',
    });
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Queue: oldest-message age (s) — scale-up signal',
        left: [queueAgeMetric],
        leftAnnotations: [{ value: 60, label: 'scale-up threshold', color: cloudwatch.Color.ORANGE },
                          { value: 135, label: 'client timeout', color: cloudwatch.Color.RED }],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Queue depth (visible messages)',
        left: [queueDepthMetric],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Worker concurrency vs throttles',
        left: [workerConcurrencyMetric],
        right: [workerThrottleMetric],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'OpenAI rate-limits (5m) — is OpenAI the wall?',
        left: [openAiThrottleMetric],
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Media Lambda errors (yt-dlp / cookies)',
        left: mediaErrorMetrics,
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'DLQ depth (hard failures)',
        left: [dlqDepthMetric],
        width: 12,
      }),
    );

    // -----------------------------
    // Outputs — copy the RecipeEnqueue ARN into CookiGatewayCDK/config/services.ts
    // (recipeImport.lambdaArn) so the gateway can front it.
    // -----------------------------
    new cdk.CfnOutput(this, 'RecipeEnqueueArn', {
      value: recipeEnqueue.functionArn,
      description: 'ARN of the recipe-import producer Lambda — onboard to the API gateway',
    });
    new cdk.CfnOutput(this, 'RecipeImportQueueUrl', {
      value: recipeImportQueue.queueUrl,
      description: 'URL of the recipe import SQS queue',
    });
  }
}