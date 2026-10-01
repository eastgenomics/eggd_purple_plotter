# Deployment — sharing sample outputs by URL

Sample `.igv.bars.html` outputs can optionally be published to S3 + CloudFront so they can
be shared with others by URL, without going through DNAnexus. This is separate from the
DNAnexus app itself — it's just a static file host for finished HTML outputs.

## Live example

https://d98wakozn5e02.cloudfront.net/145338905-26246S0070.igv.bars.html

## Where it's hosted

| | |
|---|---|
| AWS account | `342940030316` |
| SSO profile | `web-artifacts-admin` (role `AdministratorAccess`) |
| Region | `eu-west-2` |
| S3 bucket | `260930-purple-plotter-outputs` (private — all public access blocked) |
| CloudFront distribution | `E12EHUBO5DJ3G5` (`PriceClass_100`, redirect-to-https, `CachingOptimized`) |

The bucket is only readable by that one CloudFront distribution, via an Origin Access
Control scoped to its exact ARN in the bucket policy — there is no public S3 access at
all. Direct requests to the bucket's own S3 endpoint correctly return 403.

There is no `DefaultRootObject` / single index page — each sample gets its own HTML file
at `/<name>.igv.bars.html`. Share the full URL, not just the base domain.

No custom domain / Route53 / ACM certificate is configured.

## Publishing a new sample output

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY   # ambient keys override a named profile
export AWS_PROFILE=web-artifacts-admin
aws sso login   # only if the SSO session has expired

export PP_SITE_BUCKET=260930-purple-plotter-outputs
export PP_CLOUDFRONT_DISTRIBUTION_ID=E12EHUBO5DJ3G5

cp <path-to-sample>.igv.bars.html outputs/
bash infra/deploy.sh   # aws s3 sync + cloudfront invalidation
```

(Or copy the same values into `.env` and `source .env` instead of exporting manually.)

## Bootstrapping from scratch (a different account, or after deletion)

```bash
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export AWS_PROFILE=<target-profile>
export PP_SITE_BUCKET=$(date +%y%m%d)-purple-plotter-outputs   # YYMMDD- prefix convention
export AWS_REGION=eu-west-2
bash infra/bootstrap.sh
```

This scripts the entire recipe: creates the bucket, blocks all public access, creates an
Origin Access Control, creates the CloudFront distribution, and writes a bucket policy
scoped to that exact distribution. Prints the new bucket name and distribution ID to set
afterward. **Not idempotent** — one-off, human-run only, will fail if re-run against
resources that already exist.

## Naming convention

S3 bucket names in this account are prefixed with the creation date as `YYMMDD`
(2-digit year), e.g. `260930-purple-plotter-outputs` — matches the convention used by
the `cnv-atlas-overview/atlas-pipeline-explorer` project in the same account.
