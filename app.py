from __future__ import annotations

import os
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
import gspread

DEFAULT_FILE = Path("/Users/sebmargolis/Desktop/Q1 Used Car rate card analysis.xlsx")
PARTNER_SHEET = "Overall Used car partners Feb"
RENEWAL_COLUMN = "Actual renewal date"
REQUIRED_COLUMNS = {"Dealership Group Name", RENEWAL_COLUMN}
FACEBOOK_COHORT = "Facebook Group cohort"
OTHER_COHORT = "All Other Partners"
DASHBOARD_PIN = os.environ.get("DASHBOARD_PIN", "1234")
ALLOWED_NAME = "Alyx"
ALLOWED_PIN = "1020"


@st.cache_data(show_spinner=False)
def read_partner_sheet(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=PARTNER_SHEET)
    df = df.dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def normalize_colname(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def resolve_column(df: pd.DataFrame, target: str, aliases: list[str] | None = None) -> str | None:
    wanted = [target] + (aliases or [])
    normalized_to_actual = {normalize_colname(c): c for c in df.columns}
    for w in wanted:
        match = normalized_to_actual.get(normalize_colname(w))
        if match:
            return match
    return None


def resolve_renewal_column(df: pd.DataFrame) -> str | None:
    resolved = resolve_column(
        df,
        RENEWAL_COLUMN,
        aliases=["Actual Renewal Date", "Renewal Date", "renewal date"],
    )
    if resolved:
        return resolved

    # Fallback to column M (13th column) when live headers are inconsistent.
    if len(df.columns) >= 13:
        return df.columns[12]
    return None


@st.cache_data(show_spinner=False, ttl=60)
def read_partner_sheet_live(
    sheet_id: str,
    credentials_path: str | None = None,
    credentials_json: str | None = None,
    credentials_info: dict | None = None,
) -> pd.DataFrame:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    if credentials_info:
        creds = Credentials.from_service_account_info(credentials_info, scopes=scopes)
    elif credentials_json:
        creds_info = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    elif credentials_path:
        creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    else:
        raise ValueError("No Google service account credentials provided.")
    client = gspread.authorize(creds)
    ws = client.open_by_key(sheet_id).worksheet(PARTNER_SHEET)
    values = ws.get_all_values()

    if not values:
        return pd.DataFrame()

    headers = [str(c).strip() for c in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    df = df.dropna(axis=1, how="all")
    df = df.dropna(how="all").reset_index(drop=True)
    return df


def normalize_partners(df: pd.DataFrame, as_of_date: pd.Timestamp) -> pd.DataFrame:
    out = df.copy()
    partner_col = resolve_column(out, "Dealership Group Name")
    if partner_col is None:
        raise KeyError("Could not find partner column matching 'Dealership Group Name'.")
    if partner_col != "Dealership Group Name":
        out = out.rename(columns={partner_col: "Dealership Group Name"})

    renewal_col = resolve_renewal_column(out)
    if renewal_col is None:
        raise KeyError(f"Could not find renewal date column matching '{RENEWAL_COLUMN}'.")

    # Parse renewal dates as ISO first (YYYY-MM-DD), then fallback without day-first.
    raw_renewal = out[renewal_col]
    parsed_iso = pd.to_datetime(raw_renewal, format="%Y-%m-%d", errors="coerce")
    parsed_fallback = pd.to_datetime(raw_renewal, errors="coerce", dayfirst=False)
    out["Renewal Date (Working)"] = parsed_iso.fillna(parsed_fallback)
    if "CPL" in out.columns:
        out["CPL_numeric"] = pd.to_numeric(out["CPL"], errors="coerce")
        out["Cohort"] = out["CPL_numeric"].apply(
            lambda v: FACEBOOK_COHORT if v in (15.0, 18.0) else OTHER_COHORT
        )
    else:
        out["Cohort"] = OTHER_COHORT

    if "Monthly subscription cost" in out.columns:
        cost_clean = (
            out["Monthly subscription cost"]
            .astype(str)
            .str.replace(r"[^0-9.-]", "", regex=True)
        )
        out["Monthly subscription cost numeric"] = pd.to_numeric(cost_clean, errors="coerce")
    else:
        out["Monthly subscription cost numeric"] = pd.NA

    out = out.dropna(subset=["Dealership Group Name", "Renewal Date (Working)"]).copy()
    out["Days to Renewal"] = (out["Renewal Date (Working)"].dt.normalize() - as_of_date).dt.days
    out = out.sort_values(["Renewal Date (Working)", "Dealership Group Name"]).reset_index(
        drop=True
    )
    return out


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filters")

    filtered = df.copy()
    risk_col = resolve_column(filtered, "Risk banding")
    if risk_col:
        risk_values = (
            filtered[risk_col]
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .unique()
            .tolist()
        )
        risk_options = sorted(risk_values)
        selected_risk = st.sidebar.multiselect(
            "Risk banding", risk_options, default=risk_options
        )
        if selected_risk:
            filtered = filtered[
                filtered[risk_col].astype(str).str.strip().isin(selected_risk)
            ]

    if "CPL or Flat Rate" in filtered.columns:
        options = sorted(filtered["CPL or Flat Rate"].dropna().astype(str).unique().tolist())
        selected = st.sidebar.multiselect("CPL or Flat Rate", options, default=options)
        if selected:
            filtered = filtered[filtered["CPL or Flat Rate"].astype(str).isin(selected)]

    if "Dealership Group Name" in filtered.columns:
        query = st.sidebar.text_input("Partner name contains")
        if query:
            filtered = filtered[
                filtered["Dealership Group Name"]
                .astype(str)
                .str.contains(query, case=False, na=False)
            ]

    return filtered


def renewal_bucket(df: pd.DataFrame, min_days: int, max_days: int) -> pd.DataFrame:
    return df[(df["Days to Renewal"] >= min_days) & (df["Days to Renewal"] <= max_days)].copy()


def display_partner_table(df: pd.DataFrame, title: str, download_key: str) -> None:
    st.subheader(title)
    if df.empty:
        st.info("No partners in this bucket.")
        return

    preferred_cols = [
        "Dealership Group ID",
        "Dealership Group Name",
        "CPL or Flat Rate",
        "CPL",
        "Cohort",
        "Monthly subscription cost",
        "Renewal Date (Working)",
        "Days to Renewal",
    ]
    columns = [c for c in preferred_cols if c in df.columns]
    table = df[columns].copy()
    if "Renewal Date (Working)" in table.columns:
        table["Renewal Date (Working)"] = pd.to_datetime(
            table["Renewal Date (Working)"], errors="coerce"
        ).dt.date
    table = table.sort_values(
        ["Days to Renewal", "Renewal Date (Working)", "Dealership Group Name"]
    )
    st.dataframe(table, use_container_width=True, hide_index=True)

    csv = table.to_csv(index=False).encode("utf-8")
    file_stub = title.lower().replace(" ", "_")
    st.download_button(
        label=f"Download {title} CSV",
        data=csv,
        file_name=f"{file_stub}.csv",
        mime="text/csv",
        key=download_key,
    )


def display_bucket_by_cohort(df: pd.DataFrame, bucket_label: str, key_prefix: str) -> None:
    cohort_counts = (
        df["Cohort"]
        .value_counts()
        .reindex([FACEBOOK_COHORT, OTHER_COHORT], fill_value=0)
        .rename_axis("Cohort")
        .reset_index(name="Partners")
    )
    st.markdown(f"**{bucket_label} cohort counts**")
    st.dataframe(cohort_counts, use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        display_partner_table(
            df[df["Cohort"] == FACEBOOK_COHORT],
            f"{bucket_label}: {FACEBOOK_COHORT}",
            f"{key_prefix}_facebook_download",
        )
    with right:
        display_partner_table(
            df[df["Cohort"] == OTHER_COHORT],
            f"{bucket_label}: {OTHER_COHORT}",
            f"{key_prefix}_other_download",
        )


def format_currency(value: float) -> str:
    return f"£{round(value):,}"


def require_login() -> str:
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "viewer_name" not in st.session_state:
        st.session_state["viewer_name"] = ""

    if st.session_state["authenticated"]:
        return st.session_state["viewer_name"]

    st.title("Dashboard Login")
    st.caption("Enter your name and PIN to access the dashboard.")
    with st.form("login_form", clear_on_submit=False):
        name = st.text_input("Name")
        pin = st.text_input("PIN", type="password")
        submit = st.form_submit_button("Login")

    if submit:
        if not name.strip():
            st.error("Name is required.")
        elif name.strip() != ALLOWED_NAME or pin != ALLOWED_PIN:
            st.error("Invalid name or PIN.")
        else:
            st.session_state["authenticated"] = True
            st.session_state["viewer_name"] = ALLOWED_NAME
            st.rerun()

    st.stop()


def main() -> None:
    st.set_page_config(page_title="Partner Renewals Dashboard", layout="wide")
    viewer_name = require_login()
    st.markdown(
        "<h1 style='color:#1f6feb;'>Partner Renewals Dashboard</h1>",
        unsafe_allow_html=True,
    )
    st.caption(f"Focused view for sheet: {PARTNER_SHEET}")

    st.sidebar.header("Data Source")
    st.sidebar.markdown(f"Signed in as: **{viewer_name}**")
    if st.sidebar.button("Logout"):
        st.session_state["authenticated"] = False
        st.session_state["viewer_name"] = ""
        st.rerun()

    source = st.sidebar.radio(
        "Source",
        ["Google Sheet (Live)", "Local Excel"],
        index=0,
    )

    as_of = st.sidebar.date_input("As of date", value=pd.Timestamp.today().date())
    as_of_date = pd.Timestamp(as_of).normalize()

    if source == "Google Sheet (Live)":
        default_sheet_id = (
            os.environ.get("GOOGLE_SHEET_ID")
            or st.secrets.get("GOOGLE_SHEET_ID", "")
        )
        credentials_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or st.secrets.get(
            "GOOGLE_SERVICE_ACCOUNT_JSON", ""
        )
        credentials_info = st.secrets.get("gcp_service_account")
        default_creds = os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS",
            "/Users/sebmargolis/Desktop/used-car-dashboard/secrets/google-service-account.json",
        )

        sheet_id = st.sidebar.text_input("Google Sheet ID", value=default_sheet_id)
        creds_mode = st.sidebar.radio(
            "Credentials source",
            ["Streamlit/Env JSON", "Local JSON file path"],
            index=0 if (credentials_json or credentials_info) else 1,
        )
        creds_path = None
        if creds_mode == "Local JSON file path":
            creds_path = st.sidebar.text_input("Credentials JSON path", value=default_creds)
        if st.sidebar.button("Refresh live data now"):
            st.cache_data.clear()
            st.rerun()

        if not sheet_id.strip():
            st.error("Google Sheet ID is required.")
            st.stop()

        creds_file = None
        if creds_mode == "Local JSON file path":
            creds_file = Path(creds_path).expanduser()
            if not creds_file.exists():
                st.error(f"Credentials file not found: {creds_file}")
                st.stop()
        elif not (credentials_json or credentials_info):
            st.error(
                "Missing credentials in Streamlit secrets. Add GOOGLE_SERVICE_ACCOUNT_JSON or [gcp_service_account]."
            )
            st.stop()

        try:
            df = read_partner_sheet_live(
                sheet_id.strip(),
                credentials_path=str(creds_file) if creds_file else None,
                credentials_json=credentials_json or None,
                credentials_info=dict(credentials_info) if credentials_info else None,
            )
        except Exception as exc:
            st.error(f"Could not read live Google Sheet: {exc}")
            st.stop()
    else:
        custom_file = st.sidebar.text_input("Excel file path", value=str(DEFAULT_FILE))
        file_path = Path(custom_file).expanduser()
        if not file_path.exists():
            st.error(f"File not found: {file_path}")
            st.stop()
        df = read_partner_sheet(str(file_path))

    partner_col = resolve_column(df, "Dealership Group Name")
    renewal_col = resolve_renewal_column(df)
    missing = []
    if partner_col is None:
        missing.append("Dealership Group Name")
    if renewal_col is None:
        missing.append(RENEWAL_COLUMN)
    if missing:
        st.error(
            f"Missing required columns in '{PARTNER_SHEET}': {', '.join(missing)}. "
            f"Found columns: {', '.join(df.columns.astype(str).tolist())}"
        )
        st.stop()

    partners = normalize_partners(df, as_of_date)
    partners = apply_filters(partners)

    overdue = partners[partners["Days to Renewal"] < 0].copy()
    in_30 = renewal_bucket(partners, 0, 30)
    in_60 = renewal_bucket(partners, 31, 60)
    in_90 = renewal_bucket(partners, 61, 90)
    over_90 = partners[partners["Days to Renewal"] > 90].copy()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Renewing in 0-30 days", f"{len(in_30):,}")
    c2.metric("Renewing in 31-60 days", f"{len(in_60):,}")
    c3.metric("Renewing in 61-90 days", f"{len(in_90):,}")
    c4.metric("Overdue", f"{len(overdue):,}")
    c5.metric("Renewing in 90+ days", f"{len(over_90):,}")

    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric(
        "Revenue 0-30 days",
        format_currency(in_30["Monthly subscription cost numeric"].fillna(0).sum()),
    )
    r2.metric(
        "Revenue 31-60 days",
        format_currency(in_60["Monthly subscription cost numeric"].fillna(0).sum()),
    )
    r3.metric(
        "Revenue 61-90 days",
        format_currency(in_90["Monthly subscription cost numeric"].fillna(0).sum()),
    )
    r4.metric(
        "Revenue overdue",
        format_currency(overdue["Monthly subscription cost numeric"].fillna(0).sum()),
    )
    r5.metric(
        "Revenue 90+ days",
        format_currency(over_90["Monthly subscription cost numeric"].fillna(0).sum()),
    )

    st.subheader("Renewals by Cohort")
    summary = pd.DataFrame(
        {
            "Cohort": [FACEBOOK_COHORT, OTHER_COHORT],
            "0-30 days": [
                len(in_30[in_30["Cohort"] == FACEBOOK_COHORT]),
                len(in_30[in_30["Cohort"] == OTHER_COHORT]),
            ],
            "31-60 days": [
                len(in_60[in_60["Cohort"] == FACEBOOK_COHORT]),
                len(in_60[in_60["Cohort"] == OTHER_COHORT]),
            ],
            "61-90 days": [
                len(in_90[in_90["Cohort"] == FACEBOOK_COHORT]),
                len(in_90[in_90["Cohort"] == OTHER_COHORT]),
            ],
            "Overdue": [
                len(overdue[overdue["Cohort"] == FACEBOOK_COHORT]),
                len(overdue[overdue["Cohort"] == OTHER_COHORT]),
            ],
            "90+ days": [
                len(over_90[over_90["Cohort"] == FACEBOOK_COHORT]),
                len(over_90[over_90["Cohort"] == OTHER_COHORT]),
            ],
        }
    )
    summary["Total"] = summary[
        ["0-30 days", "31-60 days", "61-90 days", "90+ days", "Overdue"]
    ].sum(axis=1)
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.markdown(f"As of **{as_of_date.date()}**")
    tab30, tab60, tab90, tab_over_90, tab_overdue = st.tabs(
        ["Next 0-30 Days", "Next 31-60 Days", "Next 61-90 Days", "90+ Days", "Overdue"]
    )

    with tab30:
        display_bucket_by_cohort(in_30, "Partners Renewing in 0-30 Days", "bucket_0_30")
    with tab60:
        display_bucket_by_cohort(in_60, "Partners Renewing in 31-60 Days", "bucket_31_60")
    with tab90:
        display_bucket_by_cohort(in_90, "Partners Renewing in 61-90 Days", "bucket_61_90")
    with tab_over_90:
        display_bucket_by_cohort(over_90, "Partners Renewing in 90+ Days", "bucket_over_90")
    with tab_overdue:
        display_bucket_by_cohort(overdue, "Partners Overdue for Renewal", "bucket_overdue")


if __name__ == "__main__":
    main()
