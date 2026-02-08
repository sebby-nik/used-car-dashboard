# Used Car Dashboard

Interactive dashboard focused on partner renewals from:
- Live Google Sheet (recommended), or
- Local Excel fallback: `/Users/sebmargolis/Desktop/Q1 Used Car rate card analysis.xlsx`
- Sheet/tab: `Overall Used car partners Feb`

## Setup

```bash
cd /Users/sebmargolis/Desktop/used-car-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd /Users/sebmargolis/Desktop/used-car-dashboard
source .venv/bin/activate
streamlit run app.py
```

## Live Google Sheet setup

Set these before running:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/Users/sebmargolis/Desktop/used-car-dashboard/secrets/google-service-account.json"
export GOOGLE_SHEET_ID="your_google_sheet_id"
```

Then run:

```bash
streamlit run app.py
```

In the sidebar:
- Source: `Google Sheet (Live)`
- Confirm credentials path and sheet ID

## What it includes

- Renewal pipeline counts for 0-30, 31-60, 61-90, and 90+ days
- Dedicated partner lists in tabs for 30/60/90-day windows
- Overdue renewal list
- Cohort split:
  - `Facebook Group cohort` = partners with `CPL` 15 or 18
  - `All Other Partners` = everyone else
- Renewal counts and partner lists by both cohorts
- Filters for contract type and partner name search
- CSV download for each renewal bucket
- `As of date` picker to simulate pipeline from any date

## Notes

- The app expects `Dealership Group Name` and `Actual renewal date` columns.
- Renewal buckets are non-overlapping: `0-30`, `31-60`, `61-90` days.
- Local Excel can still be used from the sidebar source toggle.
