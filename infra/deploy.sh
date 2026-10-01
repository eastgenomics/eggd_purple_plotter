#!/usr/bin/env bash
# Syncs outputs/ (per-sample IGV bars HTML files) to the configured S3
# bucket and invalidates CloudFront.
#
# Before running: unset any ambient AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
# and export AWS_PROFILE for the target account, e.g.:
#   unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
#   export AWS_PROFILE=web-artifacts-admin
set -euo pipefail

: "${PP_SITE_BUCKET:?must be set -- see .env.example}"
: "${PP_CLOUDFRONT_DISTRIBUTION_ID:?must be set -- see .env.example}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUTS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)/outputs"

aws s3 sync "${OUTPUTS_DIR}/" "s3://${PP_SITE_BUCKET}/" \
  --exclude ".gitkeep" \
  --content-type "text/html" \
  --delete

aws cloudfront create-invalidation \
  --distribution-id "${PP_CLOUDFRONT_DISTRIBUTION_ID}" \
  --paths "/*"

echo "Deployed ${OUTPUTS_DIR} to s3://${PP_SITE_BUCKET}/ and invalidated ${PP_CLOUDFRONT_DISTRIBUTION_ID}"
