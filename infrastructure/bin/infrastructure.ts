#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { RecipeStack } from '../lib/recipe-stack';

const app = new cdk.App();

new RecipeStack(app, 'RecipeStack', {
  env: {
    account: "723402273002",
    region: "ap-southeast-2",
  },
});