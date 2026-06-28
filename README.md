# Polymarket Daily Report

Daily cloud-run report for Polymarket odds across precious metals, equities, and crypto.

## What It Does

- Pulls Polymarket market snapshots and 24-hour probability changes.
- Cross-checks with Yahoo Finance price snapshots and Google News RSS headlines.
- Generates a Chinese daily report.
- Sends the report with Gmail OAuth2.
- Runs on a GitHub Actions schedule so it keeps working when your computer is off.

## Main Files

- `scripts/polymarket_daily.py`: main report workflow
- `scripts/gmail_sender.py`: Gmail OAuth2 sender
- `.github/workflows/polymarket-daily.yml`: scheduled GitHub Actions workflow
- `docs/github-actions-polymarket.md`: deployment notes

## Required GitHub Secrets

- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`
- `GMAIL_SENDER`

Optional:

- `OPENAI_API_KEY`

Without `OPENAI_API_KEY`, the workflow still runs and falls back to a template-based report.

## Local Run

```bash
python scripts/polymarket_daily.py
```

For a no-send test:

```bash
POLYMARKET_DRY_RUN=1 python scripts/polymarket_daily.py
```
