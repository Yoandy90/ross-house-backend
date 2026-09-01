# Staging operations runbook

This runbook creates and verifies an isolated Ross House Rentals staging
environment. It does not authorize or describe a production deployment.

## Safety invariants

- Create a new staging service/environment. Never repurpose the production service.
- Use a dedicated MongoDB user and database whose names identify staging.
- Never copy production JWT, refresh, Stripe, Twilio, SendGrid, or admin tokens.
- Keep Twilio and SendGrid credentials empty unless an owner explicitly authorizes
  external delivery and the acknowledgement in the staging template is set.
- Use Stripe test keys only.
- The public hostname must contain `staging` and use HTTPS.
- Do not use `taxportal` as the database name.

## 1. Prepare the isolated backend

1. Create a new service/environment from backend `main`.
2. Provision a dedicated MongoDB staging database and least-privilege user.
3. Start from `.env.staging.example`; do not upload or commit the completed file.
4. Generate independent random values for every secret field.
5. Leave all delivery-provider credentials empty for the first verification.
6. Before configuring the service, validate the local export:

```bash
python scripts/validate_staging_env.py /absolute/path/to/staging.env
```

The command must end with `staging environment: PASS`. It never prints values.

## 2. Deploy staging only

Deploy the new staging service from the verified backend main commit. Do not
change the production service, domain, database, variables, or deployment branch.

Verify the public health endpoint:

```text
https://<service-name-containing-staging>/api/health
```

Required response properties:

- `status` is `ok`;
- `service` is `Ross House Rentals API`;
- `database` is `connected`;
- `database_name` contains `staging` and is not `taxportal`.

Stop if any property differs.

## 3. Configure the protected GitHub environment

In `Yoandy90/ross-house-backend`:

1. Open **Settings → Environments → New environment**.
2. Name the environment exactly `staging`.
3. Restrict deployment branches to `main`.
4. Add an environment variable named `STAGING_BASE_URL` containing only the
   HTTPS origin, without `/api`, query, or trailing path.
5. Create a synthetic staging administrator through the normal staging-only
   authentication flow.
6. Add its temporary access token as the environment secret
   `STAGING_ADMIN_TOKEN`.

Never paste a production token. Rotate or remove the staging token after the
verification window.

## 4. Run the read-only renewal smoke

1. Open **Actions → Staging Renewal Smoke**.
2. Choose **Run workflow** on `main`.
3. Confirm the job uses the `staging` environment.
4. Approve the environment gate if GitHub requests it.

A successful run reports these checks without exposing values:

- `health-and-database`;
- `renewal-auth-boundary`;
- `renewal-admin-read-model`.

The smoke performs GET requests only. It creates no proposals, contracts,
signatures, payments, occupancy changes, notifications, or provider calls.

## 5. Interpret failures

| Failure | Required action |
| --- | --- |
| Refusing production-looking host | Correct `STAGING_BASE_URL`; never bypass the guard. |
| Hostname must contain staging | Assign an explicit staging hostname. |
| Staging target must use HTTPS | Configure TLS before retrying. |
| Dedicated staging database error | Correct the service DB variables and redeploy staging. |
| Anonymous access was not rejected | Stop; treat as an authorization regression. |
| Admin read model unavailable | Rotate the temporary token and verify staging auth. |
| Canonical read-only model error | Stop; investigate backend/API version drift. |

Do not weaken a guard to make a failed run pass.

## 6. Evidence and go/no-go

Record the backend main SHA, workflow run URL, timestamp, staging hostname,
and PASS/FAIL result. Do not record tokens, credentials, tenant contact data,
provider evidence, or database connection strings.

A PASS authorizes only the next staging test phase. It does not authorize
production, live providers, real tenant data, or mobile release.

The next phase is a controlled synthetic renewal lifecycle in staging:
proposal, approval, tenant response, contract generation, signatures, rollover,
and recovery verification. It requires synthetic identities, providers disabled,
cleanup evidence, and a separate go/no-go decision.

## Rollback and shutdown

If staging is misconfigured:

1. Stop the staging service.
2. Revoke the synthetic staging admin session.
3. Remove or rotate `STAGING_ADMIN_TOKEN`.
4. Correct variables using the validator before redeploying staging.
5. Preserve logs that contain no secrets for diagnosis.

Deleting a staging database is a separate destructive action. Confirm its exact
name, take any required export, and obtain explicit authorization before deletion.
Never alter the production service or database as part of staging rollback.
