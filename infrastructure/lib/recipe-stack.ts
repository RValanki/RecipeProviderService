import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as path from 'path';
import { Construct } from 'constructs';

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
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || '',
      },
    });

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
      timeout: cdk.Duration.seconds(60),
      memorySize: 512,
      environment: {
        OPENAI_API_KEY: process.env.OPENAI_API_KEY || '',
        MEDIA_LAMBDA_NAME: tikTokMediaProcessor.functionName,
      },
    });

    // -----------------------------
    // Grant RecipeProcessor permission to invoke TikTokMediaProcessor
    // -----------------------------
    tikTokMediaProcessor.grantInvoke(recipeProcessor);
  }
}