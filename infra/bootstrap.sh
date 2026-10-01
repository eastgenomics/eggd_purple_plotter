#!/usr/bin/env bash
# One-off bootstrap: create a private S3 bucket + CloudFront distribution
# (with Origin Access Control, so the bucket itself never needs to be
# public) for hosting eggd_purple_plotter IGV bars HTML outputs so they can
# be shared by URL. Run once by a human, not part of CI -- re-running is
# not idempotent (it will try to create the bucket/OAC/distribution again
# and fail if they already exist).
#
# Before running: unset any ambient AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY
# and export AWS_PROFILE for the target account, e.g.:
#   unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
#   export AWS_PROFILE=web-artifacts-admin
set -euo pipefail

: "${PP_SITE_BUCKET:?must be set -- see .env.example. Convention: prefix with the current date as YYMMDD, e.g. 260930-purple-plotter-outputs}"
AWS_REGION="${AWS_REGION:-eu-west-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "Creating bucket ${PP_SITE_BUCKET} in ${AWS_REGION} (account ${ACCOUNT_ID})..."
aws s3api create-bucket \
  --bucket "${PP_SITE_BUCKET}" \
  --region "${AWS_REGION}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}"

aws s3api put-public-access-block \
  --bucket "${PP_SITE_BUCKET}" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "Creating Origin Access Control..."
OAC_ID="$(aws cloudfront create-origin-access-control \
  --origin-access-control-config "{
    \"Name\": \"${PP_SITE_BUCKET}-oac\",
    \"Description\": \"OAC for eggd_purple_plotter outputs S3 origin\",
    \"SigningProtocol\": \"sigv4\",
    \"SigningBehavior\": \"always\",
    \"OriginAccessControlOriginType\": \"s3\"
  }" \
  --query "OriginAccessControl.Id" --output text)"
echo "OAC created: ${OAC_ID}"

echo "Creating CloudFront distribution..."
DIST_CONFIG="$(cat <<EOF
{
  "CallerReference": "${PP_SITE_BUCKET}-$(date +%s 2>/dev/null || echo bootstrap)",
  "Comment": "eggd_purple_plotter outputs",
  "Enabled": true,
  "PriceClass": "PriceClass_100",
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "s3-origin",
        "DomainName": "${PP_SITE_BUCKET}.s3.${AWS_REGION}.amazonaws.com",
        "OriginAccessControlId": "${OAC_ID}",
        "S3OriginConfig": { "OriginAccessIdentity": "" }
      }
    ]
  },
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3-origin",
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] },
    "Compress": true,
    "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }
}
EOF
)"
DIST_RESULT="$(aws cloudfront create-distribution --distribution-config "${DIST_CONFIG}")"
DIST_ID="$(echo "${DIST_RESULT}" | python3 -c "import json,sys; print(json.load(sys.stdin)['Distribution']['Id'])")"
DIST_DOMAIN="$(echo "${DIST_RESULT}" | python3 -c "import json,sys; print(json.load(sys.stdin)['Distribution']['DomainName'])")"
echo "Distribution created: ${DIST_ID} (${DIST_DOMAIN}) -- deploying, can take 5-15 minutes"

echo "Restricting bucket access to only this distribution..."
aws s3api put-bucket-policy --bucket "${PP_SITE_BUCKET}" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {
      \"Sid\": \"AllowCloudFrontServicePrincipalReadOnly\",
      \"Effect\": \"Allow\",
      \"Principal\": { \"Service\": \"cloudfront.amazonaws.com\" },
      \"Action\": \"s3:GetObject\",
      \"Resource\": \"arn:aws:s3:::${PP_SITE_BUCKET}/*\",
      \"Condition\": {
        \"StringEquals\": { \"AWS:SourceArn\": \"arn:aws:cloudfront::${ACCOUNT_ID}:distribution/${DIST_ID}\" }
      }
    }
  ]
}"

cat <<EOF

Done. Bucket is private -- only this CloudFront distribution can read it.
No DefaultRootObject is set (there is no single index page, just per-sample
HTML files) -- always share the full /<sample>.igv.bars.html URL.

Set these in .env, then run: infra/deploy.sh
  PP_SITE_BUCKET=${PP_SITE_BUCKET}
  PP_CLOUDFRONT_DISTRIBUTION_ID=${DIST_ID}

Base URL once deployed and the distribution finishes deploying:
  https://${DIST_DOMAIN}/
EOF
