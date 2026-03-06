# Dashboard Portfolio Runbook

Updated: 2026-03-06

## Purpose

This runbook covers the dashboard `/api/portfolio` contract after the dual-account hardening work:

- top-level `status` is `ok`, `partial`, or `error`
- `position_count_by_account` is present for every requested account, including zero-count error accounts
- `account_errors` surfaces account-specific failures without dropping healthy account data
- `consistency` exposes count checks so operators can validate what the UI is rendering

## Local Verification

1. Run the focused regression tests:

```bash
python -m pytest tests/dashboard/test_portfolio_api.py tests/dashboard/test_alpaca_adapter_constraints.py -v --tb=short
```

2. If the dashboard API is running locally, verify the live payload shape:

```bash
curl -s http://127.0.0.1:8501/api/portfolio?account=all | python3 scripts/verify/portfolio_schema_check.py
```

3. Check the dashboard UI:

- Portfolio panel shows a status chip (`OK`, `PARTIAL`, `ERROR`)
- Per-account cards show both healthy and failed accounts
- Partial failures show the broker/account error text instead of silently hiding the bad account
- Duplicate symbols across accounts keep their account tag in the positions list

## Live Deploy Verification

After a dashboard/API redeploy, run the live parity check from the repo root:

```bash
python3 scripts/verify/live_dashboard_deploy_check.py \
  --url http://20.124.180.8:8501 \
  --api-key "$GS_DASHBOARD_API_KEY"
```

This verifies both surfaces:

- the live `/api/portfolio` payload includes the latest lane-B schema keys
- the live `/api/execution/summary` payload includes the latest execution widget schema keys
- the live dashboard root is serving the same built app chunk as the local frontend output
- the live dashboard bundle still contains the execution widget markers (`Routing Funnel`, `True Fill Rate`, `Skip / Block Categories`)

For a GitHub-triggered post-deploy check, use the manual workflow:

- `.github/workflows/dashboard-live-verify.yml`

That workflow builds the frontend on the runner and compares it against the live dashboard URL, using the `GS_DASHBOARD_API_KEY` repository secret.

## Expected Response Semantics

- `status: ok`
  All requested accounts returned successfully.
- `status: partial`
  At least one account returned successfully and at least one failed.
- `status: error`
  Every requested account failed.

When `status` is `partial` or `error`, `account_errors` should be non-empty and `position_count_by_account` should still contain every requested account label.

## Rollback

If the portfolio widget regresses after deploy, roll back the smallest surface that fixes the issue.

### Frontend-only rollback

Use this when `/api/portfolio` is healthy but the dashboard is rendering the wrong state.

1. Revert the dashboard frontend files to the last known-good revision:
   - `dashboard/frontend/src/lib/api.ts`
   - `dashboard/frontend/src/components/PortfolioPanel.tsx`
   - `dashboard/frontend/src/components/PerformancePanel.tsx`
2. Rebuild/redeploy the dashboard frontend.
3. Re-run the local verification steps above.

### API rollback

Use this when the live `/api/portfolio` payload is missing keys, has inconsistent counts, or always reports `error`.

1. Revert `dashboard/api/server.py` to the last known-good revision.
2. Restart the dashboard API service.
3. Re-run:

```bash
python -m pytest tests/dashboard/test_portfolio_api.py -v --tb=short
curl -s http://127.0.0.1:8501/api/portfolio?account=all | python3 scripts/verify/portfolio_schema_check.py
python3 scripts/verify/live_dashboard_deploy_check.py --url http://20.124.180.8:8501 --api-key "$GS_DASHBOARD_API_KEY"
```

### Full dashboard rollback

Use this when both the API response and UI are suspect after the same deploy.

1. Revert the API and frontend files together.
2. Redeploy the dashboard bundle.
3. Re-run both test and live payload verification before closing the incident.
