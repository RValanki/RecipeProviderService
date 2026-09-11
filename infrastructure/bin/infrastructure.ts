#!/usr/bin/env node
import 'source-map-support/register';
import * as dotenv from 'dotenv';
import * as path from 'path';
// Load the shared .env (one level above /infrastructure) so `cdk deploy` picks
// up OPENAI_API_KEY / FIREBASE_SERVICE_ACCOUNT without manually sourcing it.
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

import * as cdk from 'aws-cdk-lib';
import { RecipeStack } from '../lib/recipe-stack';

const app = new cdk.App();

new RecipeStack(app, 'RecipeStack', {
  env: {
    account: "723402273002",
    region: "ap-southeast-2",
  },
});