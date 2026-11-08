"""AI Collections Employee - Streamlit dashboard (audited build of app.py)."""

import io
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

# ------------------------------------------------------------
# Page configuration (must be the first Streamlit command)
# ------------------------------------------------------------
st.set_page_config(
    page_title="AI Collections Employee",
    page_icon=":shark:",
    layout="wide",
)

# Project modules are imported after set_page_config on purpose, so that a module
# touching Streamlit at import time can never break the "first command" rule.
from approval_service import STATUSES
from approval_service import initialize as initialize_approvals
from audit_service import initialize_audit, list_events, log_event
from auth_service import (
    ROLES,
    authenticate,
    create_user,
    current_user,
    initialize_auth,
    list_users,
    logout,
    set_user_active,
)
from collection_agent_v3_5 import AgentWorkflowError, run_collection_agent
from collection_rules import decide_collection_action
from db_operations import check_database_integrity, create_backup, get_database_info
from email_draft_service_v3_8 import (
    edit_email_draft,
    get_email_draft,
    list_email_draft_versions,
    list_email_drafts,
    recover_email_draft_submissions,
    save_email_draft,
    submit_email_draft_for_approval,
    sync_email_draft_approval_status,
    validate_email_draft,
)
from email_provider_service_v3_8 import (
    list_send_attempts,
    provider_status,
    recover_stale_send_attempts,
    send_approved_email,
)
from evaluation_service import run_evaluation
from follow_up_service import (
    TASK_STATUSES,
    TASK_TYPES,
    create_task,
    initialize_database,
    list_activity,
    list_tasks,
    update_task_status,
)
from llm_service import LLMServiceError, generate_payment_reminder
from pilot_readiness_service import (
    READINESS_CHECKLIST,
    calculate_pilot_kpis,
    list_pilot_metrics,
    record_pilot_metrics,
    run_classification_benchmark,
)
from response_classification_service import (
    ResponseClassificationError,
    classify_customer_response,
)
from secure_workflow_service import (
    create_approval_request,
    decide_approval_request,
    initialize_secure_workflows,
    list_authorized_approvals,
    list_workflow_links,
)
from workflow_orchestrator_v3_6 import (
    create_workflow,
    get_workflow,
    list_workflow_steps,
    list_workflows,
    run_or_resume_workflow,
)
from workflow_orchestrator_v3_6 import initialize as initialize_workflow_engine
from workflow_reliability_service import list_reliability_events  # noqa: F401

# ------------------------------------------------------------
# Constants and shared helpers
# ------------------------------------------------------------
SESSION_IDLE_TIMEOUT_MINUTES = 30
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_INVOICE_ROWS = 100_000

# Streamlit >= 1.50 replaces use_container_width with width="stretch"; older
# versions only understand use_container_width. Pick whichever this install supports.
try:
    from packaging.version import Version

    STRETCH = (
        {"width": "stretch"}
        if Version(st.__version__) >= Version("1.50.0")
        else {"use_container_width": True}
    )
except Exception:  # noqa: BLE001
    STRETCH = {"use_container_width": True}

_MD_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<&$:])")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def md_escape(value) -> str:
    """Escape Markdown so uploaded or AI-generated text cannot render links, images or formatting."""
    return _MD_SPECIALS.sub(r"\\\1", "" if value is None else str(value))


def _neutralize_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def csv_bytes(frame: pd.DataFrame, encoding: str = "utf-8") -> bytes:
    """Serialise a DataFrame to CSV, defusing spreadsheet formula injection (=, +, -, @)."""
    safe = frame.copy()
    for column in safe.columns:
        series = safe[column]
        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            safe[column] = series.map(_neutralize_cell)
    return safe.to_csv(index=False).encode(encoding)


def drop_sensitive_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Never display password/hash/salt/secret columns, even if a service returns them."""
    blocked = ("password", "hash", "salt", "secret", "token")
    keep = [c for c in frame.columns if not any(word in str(c).lower() for word in blocked)]
    return frame[keep]


def flash(level: str, message: str) -> None:
    """Queue a message that survives the next st.rerun() (messages shown before a rerun vanish)."""
    st.session_state.setdefault("_flash_messages", []).append((level, message))


def flash_and_rerun(level: str, message: str) -> None:
    flash(level, message)
    st.rerun()


def render_flashes() -> None:
    for level, message in st.session_state.pop("_flash_messages", []):
        {"success": st.success, "warning": st.warning, "error": st.error}.get(level, st.info)(message)


def reset_session(notice: Optional[str] = None) -> None:  # noqa: UP045
    """Sign out and wipe every per-user value held in this browser session."""
    try:
        logout()
    finally:
        for key in list(st.session_state.keys()):
            try:
                del st.session_state[key]
            except Exception:  # noqa: BLE001, S110
                pass
        if notice:
            st.session_state["_login_notice"] = notice


# ------------------------------------------------------------
# Header
# ------------------------------------------------------------
st.title("AI Collections Employee")
st.caption("V4.1 — Pre-Production Security Hardening (in progress)")
st.caption("Accounts Receivable Intelligence Dashboard · Local prototype; not production-certified")


# ------------------------------------------------------------
# Authentication gate (protects the UI surface; not a substitute for production SSO)
# ------------------------------------------------------------
initialize_auth()
initialize_audit()

# Server-side idle timeout: defence in depth, not a replacement for SSO/session controls.
_now_utc = datetime.now(timezone.utc)  # noqa: UP017
if st.session_state.get("v34_user") is not None:
    _last_activity = st.session_state.get("_security_last_activity_utc")
    _session_problem = None
    if _last_activity:
        try:
            _last_dt = datetime.fromisoformat(_last_activity)
            if _last_dt.tzinfo is None:
                _last_dt = _last_dt.replace(tzinfo=timezone.utc)  # noqa: UP017
            if _now_utc - _last_dt > timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES):
                _session_problem = (
                    "session_idle_timeout",
                    f"Your session expired after {SESSION_IDLE_TIMEOUT_MINUTES} minutes of inactivity. "  # noqa: ISC004
                    "Please sign in again.",
                )
        except (TypeError, ValueError):
            # A malformed timestamp is treated as expired, never as an authenticated session.
            _session_problem = ("session_invalid", "Your session state was invalid. Please sign in again.")
    if _session_problem:
        _expired_user = st.session_state.get("v34_user")
        if not isinstance(_expired_user, dict):
            _expired_user = {}
        try:
            log_event(
                _expired_user.get("username", "unknown"),
                _expired_user.get("role", "unknown"),
                _session_problem[0],
                "auth",
                "session_expired",
            )
        finally:
            reset_session(_session_problem[1])
        st.rerun()
    st.session_state["_security_last_activity_utc"] = _now_utc.isoformat()

_user = current_user()
if _user is None:
    st.subheader("Sign in")
    _login_notice = st.session_state.pop("_login_notice", None)
    if _login_notice:
        st.warning(_login_notice)
    st.info(
        "V3.4 local authentication is enabled. Create the first administrator using "
        "manage_users.py before signing in."
    )
    with st.form("v34_login_form"):
        _username = st.text_input("Username", max_chars=80)
        _password = st.text_input("Password", type="password", max_chars=256)
        _login_submit = st.form_submit_button("Sign in", type="primary")
    if _login_submit:
        _authenticated = authenticate(_username, _password)
        if _authenticated:
            st.session_state["v34_user"] = _authenticated
            st.session_state["_security_last_activity_utc"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
            st.session_state.pop("_security_app_access_logged", None)
            log_event(_authenticated["username"], _authenticated["role"], "login_success", "auth", _authenticated["username"])
            st.rerun()
        else:
            log_event((_username or "unknown")[:80], "unknown", "login_failure", "auth", "invalid credentials")
            st.error("Sign-in failed. Check your credentials or ask the administrator.")
    st.stop()

st.sidebar.success(f"Signed in: {md_escape(_user['username'])} · {md_escape(_user['role'])}")
if st.sidebar.button("Sign out", key="v34_logout"):
    try:
        log_event(_user["username"], _user["role"], "logout", "auth", _user["username"])
    finally:
        reset_session()
    st.rerun()

# Avoid writing an audit row on every Streamlit rerun: log once per authenticated session.
if not st.session_state.get("_security_app_access_logged"):
    log_event(_user["username"], _user["role"], "app_access", "application", "opened app")
    st.session_state["_security_app_access_logged"] = True

# Initialise every service once, in dependency order, before any section uses its tables.
initialize_database()
initialize_approvals()
initialize_secure_workflows()
initialize_workflow_engine()

render_flashes()


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------
REQUIRED_COLUMNS = [
    "invoice_number",
    "customer_name",
    "customer_email",
    "invoice_date",
    "due_date",
    "invoice_amount",
    "outstanding_amount",
    "dispute_status",
]

SAMPLE_FILE = Path(__file__).resolve().parent / "sample_invoices.csv"


# ------------------------------------------------------------
# Data processing
# ------------------------------------------------------------
def process_invoices(data: pd.DataFrame) -> pd.DataFrame:
    """Validate invoice data and calculate aging and priority fields."""
    if data is None or data.empty:
        raise ValueError("The invoice file is empty. Please upload data rows.")
    if len(data) > MAX_INVOICE_ROWS:
        raise ValueError(f"The file has {len(data):,} rows; the limit is {MAX_INVOICE_ROWS:,}.")

    data = data.copy()

    # Normalise column names, including a UTF-8 BOM and accidental surrounding spaces.
    data.columns = [str(column).replace("\ufeff", "").strip().lower() for column in data.columns]

    repeated = data.columns[data.columns.duplicated()].unique().tolist()
    if repeated:
        raise ValueError("Duplicate column name(s) in the file: " + ", ".join(map(str, repeated)))

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    if missing_columns:
        raise ValueError("Missing required columns: " + ", ".join(missing_columns))

    # Normalise text fields.
    for column in ["invoice_number", "customer_name", "customer_email", "dispute_status"]:
        data[column] = data[column].fillna("").astype(str).str.strip()

    # Normalise dates and amounts.
    for column in ["invoice_date", "due_date"]:
        data[column] = pd.to_datetime(data[column], errors="coerce")

    for column in ["invoice_amount", "outstanding_amount"]:
        # Accept values such as "₹1,500", "Rs. 1,500" or "INR 1500" as well as plain numbers.
        cleaned = (
            data[column]
            .astype(str)
            .str.replace(r"(?i)\b(?:inr|rs\.?)", "", regex=True)
            .str.replace("₹", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        data[column] = pd.to_numeric(cleaned, errors="coerce")

    # Required-field validation.
    invalid = (
        data["invoice_number"].eq("")
        | data["customer_name"].eq("")
        | data["due_date"].isna()
        | data["invoice_amount"].isna()
        | data["outstanding_amount"].isna()
        | data["invoice_amount"].lt(0)
        | data["outstanding_amount"].lt(0)
        | data["outstanding_amount"].gt(data["invoice_amount"])
    )

    if invalid.any():
        bad_rows = (data.index[invalid.to_numpy()] + 2).tolist()[:10]
        row_text = ", ".join(map(str, bad_rows))
        raise ValueError(
            f"{int(invalid.sum())} row(s) have missing or invalid required values. "
            f"Example CSV row number(s): {row_text}."
        )

    # Invoice numbers must be unique within the uploaded file.
    duplicates = data["invoice_number"].duplicated(keep=False)
    if duplicates.any():
        duplicate_ids = data.loc[duplicates, "invoice_number"].drop_duplicates().tolist()[:10]
        raise ValueError(
            "Duplicate invoice number(s) detected: "
            + ", ".join(duplicate_ids)
            + ". Please resolve duplicates before analysis."
        )

    # Use today's local system date to calculate overdue days.
    today = pd.Timestamp(date.today())
    due_dates = data["due_date"].dt.normalize()
    data["days_overdue"] = (today - due_dates).dt.days.clip(lower=0).astype(int)

    # Invoice status.
    data["invoice_status"] = "Open"
    data.loc[data["outstanding_amount"].eq(0), "invoice_status"] = "Paid"
    data.loc[
        data["outstanding_amount"].gt(0) & due_dates.ge(today),
        "invoice_status",
    ] = "Not Due"
    data.loc[
        data["outstanding_amount"].gt(0) & due_dates.lt(today),
        "invoice_status",
    ] = "Overdue"

    # Aging bucket. Invoices due today are Current (0 days overdue).
    data["aging_bucket"] = pd.cut(
        data["days_overdue"],
        bins=[-1, 0, 30, 60, 90, float("inf")],
        labels=["Current", "1-30 days", "31-60 days", "61-90 days", "90+ days"],
    )

    # Basic, explainable prioritisation rules.
    def get_priority(row):
        if row["outstanding_amount"] == 0:
            return "Closed"

        dispute = str(row["dispute_status"]).strip().lower()
        if dispute not in {"", "none", "no", "false", "nan", "not disputed"}:
            return "Review"

        if row["days_overdue"] >= 60:
            return "High"
        if row["days_overdue"] >= 30:
            return "Medium"
        if row["days_overdue"] > 0:
            return "Low"
        return "Not Due"

    data["priority"] = data.apply(get_priority, axis=1)

    return data


# ------------------------------------------------------------
# File loading
# ------------------------------------------------------------
def read_invoice_file(file_name: str, content: bytes) -> pd.DataFrame:
    """Read a CSV/XLSX upload as text so invoice numbers keep leading zeros and BOMs are handled."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError(f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")

    lowered = file_name.lower()
    if lowered.endswith(".csv"):
        last_error = None
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                return pd.read_csv(
                    io.BytesIO(content), dtype=str, keep_default_na=False, encoding=encoding
                )
            except UnicodeDecodeError as error:
                last_error = error
        raise ValueError(
            "The CSV file could not be decoded. Save it as UTF-8 and upload it again."
        ) from last_error
    if lowered.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    raise ValueError("Unsupported file type. Upload a .csv or .xlsx file.")


uploaded_file = st.file_uploader(
    "Upload invoice data",
    type=["csv", "xlsx"],
    help="Required columns: " + ", ".join(REQUIRED_COLUMNS),
)

raw_data = None

if uploaded_file is not None:
    st.session_state["use_sample_data"] = False
    try:
        raw_data = read_invoice_file(uploaded_file.name, uploaded_file.getvalue())
    except Exception as error:  # noqa: BLE001
        st.error(f"Unable to read the uploaded file: {error}")
        st.stop()
elif st.session_state.get("use_sample_data"):
    # The flag keeps sample mode alive across reruns; a plain button press lasts only one run.
    if not SAMPLE_FILE.exists():
        st.session_state["use_sample_data"] = False
        st.error(
            "sample_invoices.csv was not found next to app.py. "
            "Upload your own CSV/XLSX file or place the sample file beside app.py."
        )
        st.stop()
    try:
        raw_data = read_invoice_file(SAMPLE_FILE.name, SAMPLE_FILE.read_bytes())
    except Exception as error:  # noqa: BLE001
        st.session_state["use_sample_data"] = False
        st.error(f"Unable to load sample data: {error}")
        st.stop()
    st.caption("Showing the bundled sample data.")
    if st.sidebar.button("Stop using sample data", key="stop_sample_data"):
        st.session_state["use_sample_data"] = False
        st.rerun()
else:
    st.info("Upload a CSV or Excel invoice file, or load the included sample data.")
    if st.button("Load sample data", type="primary", key="load_sample_data"):
        st.session_state["use_sample_data"] = True
        st.rerun()
    st.stop()


# ------------------------------------------------------------
# Validate and prepare data
# ------------------------------------------------------------
try:
    invoices = process_invoices(raw_data)
except Exception as error:  # noqa: BLE001
    st.error(f"Invoice validation failed: {error}")
    st.caption("Check that the CSV/XLSX contains the required column names and valid rows.")
    st.stop()

open_invoices = invoices.loc[invoices["outstanding_amount"] > 0].copy()
overdue_invoices = open_invoices.loc[open_invoices["days_overdue"] > 0].copy()
high_priority = open_invoices.loc[open_invoices["priority"] == "High"].copy()


# ------------------------------------------------------------
# KPI metrics
# ------------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)

col1.metric("Total Outstanding", f"₹{open_invoices['outstanding_amount'].sum():,.0f}")
col2.metric("Overdue Amount", f"₹{overdue_invoices['outstanding_amount'].sum():,.0f}")
col3.metric("Overdue Invoices", f"{len(overdue_invoices):,}")
col4.metric("High Priority", f"{len(high_priority):,}")

st.divider()


# ------------------------------------------------------------
# Invoice explorer
# ------------------------------------------------------------
st.subheader("Invoice Explorer")
search = st.text_input("Search invoice or customer")

filtered = invoices.copy()
if search.strip():
    query = search.strip()
    mask = (
        filtered["invoice_number"].str.contains(query, case=False, na=False, regex=False)
        | filtered["customer_name"].str.contains(query, case=False, na=False, regex=False)
    )
    filtered = filtered.loc[mask]

display_columns = [
    "invoice_number",
    "customer_name",
    "invoice_amount",
    "outstanding_amount",
    "days_overdue",
    "aging_bucket",
    "dispute_status",
    "invoice_status",
    "priority",
]

st.dataframe(filtered[display_columns], hide_index=True, **STRETCH)

st.divider()


# ------------------------------------------------------------
# Charts
# ------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Outstanding by Aging Bucket")

    if overdue_invoices.empty:
        st.info("No overdue invoices were found.")
    else:
        aging_summary = (
            overdue_invoices.groupby("aging_bucket", observed=False)["outstanding_amount"]
            .sum()
            .reset_index()
        )
        aging_summary = aging_summary.loc[aging_summary["outstanding_amount"] > 0].copy()
        aging_summary["aging_bucket"] = aging_summary["aging_bucket"].astype(str)

        if aging_summary.empty:
            st.info("No overdue balance is available to chart.")
        else:
            figure = px.bar(
                aging_summary,
                x="aging_bucket",
                y="outstanding_amount",
                title="Overdue Balance by Aging",
                labels={
                    "aging_bucket": "Aging Bucket",
                    "outstanding_amount": "Outstanding Amount (₹)",
                },
                category_orders={"aging_bucket": ["1-30 days", "31-60 days", "61-90 days", "90+ days"]},
            )
            figure.update_layout(xaxis_title="Aging Bucket", yaxis_title="Outstanding Amount (₹)")
            st.plotly_chart(figure, **STRETCH)

with right:
    st.subheader("Collection Priorities")

    if open_invoices.empty:
        st.info("No outstanding invoices were found.")
    else:
        priority_summary = open_invoices.groupby("priority").size().reset_index(name="invoice_count")
        figure = px.pie(
            priority_summary,
            names="priority",
            values="invoice_count",
            title="Open Invoices by Priority",
        )
        st.plotly_chart(figure, **STRETCH)


# ------------------------------------------------------------
# High-priority queue
# ------------------------------------------------------------
st.divider()
st.subheader("High-Priority Collection Queue")

if high_priority.empty:
    st.info("There are no invoices currently marked High priority.")
else:
    st.dataframe(
        high_priority[
            [
                "invoice_number",
                "customer_name",
                "customer_email",
                "outstanding_amount",
                "days_overdue",
                "dispute_status",
            ]
        ],
        hide_index=True,
        **STRETCH,
    )


# ------------------------------------------------------------
# AI payment reminder generator
# ------------------------------------------------------------
if _user["role"] in ("admin", "reviewer"):
    try:
        recover_email_draft_submissions(_user)
    except Exception as _recovery_error:  # noqa: BLE001
        st.warning(
            "Email draft recovery/synchronization needs review: "
            f"{type(_recovery_error).__name__}: {_recovery_error}"
        )

st.divider()
st.header("AI Payment Reminder Generator")
st.caption(
    "Generate an editable reminder draft from an overdue invoice. This generator does not send "
    "email; delivery is possible only from an approved draft in the delivery section below."
)

# Only offer overdue invoices with a balance and no unresolved dispute.
eligible_invoices = invoices.loc[
    (invoices["outstanding_amount"] > 0)
    & (invoices["days_overdue"] > 0)
    & (invoices["priority"] != "Review")
].copy()

if eligible_invoices.empty:
    st.info(
        "No eligible overdue invoices are available. "
        "Check your invoice data or select another dataset."
    )
else:
    eligible_invoices["invoice_label"] = eligible_invoices.apply(
        lambda row: (
            f"{row['invoice_number']} | {row['customer_name']} | "
            f"₹{row['outstanding_amount']:,.2f} | "
            f"{int(row['days_overdue'])} days overdue"
        ),
        axis=1,
    )

    selected_label = st.selectbox(
        "Select an overdue invoice",
        options=eligible_invoices["invoice_label"].tolist(),
        key="reminder_selected_invoice",
    )
    selected_invoice = eligible_invoices.loc[
        eligible_invoices["invoice_label"] == selected_label
    ].iloc[0]
    selected_invoice_number = str(selected_invoice["invoice_number"])

    tone = st.selectbox(
        "Reminder tone",
        options=["Professional", "Friendly", "Firm"],
        index=0,
        key="reminder_tone",
    )

    st.write("### Selected invoice details")
    detail_col1, detail_col2, detail_col3 = st.columns(3)
    detail_col1.metric("Outstanding amount", f"₹{selected_invoice['outstanding_amount']:,.2f}")
    detail_col2.metric("Days overdue", int(selected_invoice["days_overdue"]))
    detail_col3.write(f"**Customer:** {md_escape(selected_invoice['customer_name'])}")

    st.write(f"**Invoice number:** {md_escape(selected_invoice_number)}")
    st.write(f"**Customer email:** {md_escape(selected_invoice['customer_email'] or 'Not provided')}")
    st.write(f"**Due date:** {selected_invoice['due_date'].strftime('%d %b %Y')}")

    if st.button("Generate AI Payment Reminder", type="primary", key="generate_reminder_button"):
        with st.spinner("Generating payment reminder..."):
            try:
                new_draft = generate_payment_reminder(
                    invoice_number=selected_invoice_number,
                    customer_name=str(selected_invoice["customer_name"]),
                    outstanding_amount=float(selected_invoice["outstanding_amount"]),
                    due_date=selected_invoice["due_date"].strftime("%d %b %Y"),
                    days_overdue=int(selected_invoice["days_overdue"]),
                    reminder_tone=tone,
                )
                if not isinstance(new_draft, dict):
                    raise ValueError("The reminder service returned an unexpected response format.")  # noqa: TRY004

                # Store the structured response. A fresh nonce gives the editor widgets new keys,
                # so a regenerated draft is never hidden behind stale text from an earlier one.
                st.session_state["generated_email_draft"] = new_draft
                st.session_state["generated_email_invoice"] = selected_invoice_number
                st.session_state["generated_email_tone"] = tone
                st.session_state["generated_email_nonce"] = uuid.uuid4().hex[:8]
                st.session_state["email_draft_saved"] = False
                for _stale_key in (
                    "saved_email_draft_id",
                    "saved_email_draft_invoice",
                    "saved_email_draft_content",
                    "last_email_validation",
                    "last_email_validation_invoice",
                    "last_email_validation_content",
                ):
                    st.session_state.pop(_stale_key, None)
                st.rerun()

            except LLMServiceError as error:
                st.error(str(error))
            except Exception as error:  # noqa: BLE001
                st.error(
                    "An unexpected error occurred while generating the draft: "
                    f"{type(error).__name__}: {error}"
                )

    draft_invoice = st.session_state.get("generated_email_invoice")
    draft = st.session_state.get("generated_email_draft")

    # Do not show a draft generated for a different invoice.
    if draft_invoice == selected_invoice_number and isinstance(draft, dict):
        _nonce = st.session_state.get("generated_email_nonce", "0")
        _draft_review_ok = draft.get("requires_human_review") is True

        st.divider()
        st.subheader("Review and edit the email draft")

        st.info(
            f"Generated in {md_escape(draft.get('tone', tone))} tone. "
            "Please verify all facts before using it."
        )

        draft_recipient = st.text_input(
            "Recipient email (draft-only; verify against your approved customer records)",
            value=str(selected_invoice.get("customer_email") or ""),
            key=f"email_draft_recipient_{selected_invoice_number}_{_nonce}",
            max_chars=254,
        )
        st.caption("Only one recipient is permitted.")
        current_subject = st.text_input(
            "Email subject",
            value=str(draft.get("subject", ""))[:200],
            key=f"edited_email_subject_{_nonce}",
            max_chars=200,
        )
        current_body = st.text_area(
            "Email body",
            value=str(draft.get("body", "")),
            key=f"edited_email_body_{_nonce}",
            height=280,
        )

        st.write("### Recommended next action")
        st.markdown(md_escape(draft.get("recommended_action") or "No recommendation returned."))

        if _draft_review_ok:
            st.warning("Human review is required before this draft can be used.")
        else:
            st.error("The response did not require human review. Do not use this draft.")

        current_content = (draft_recipient, current_subject, current_body)
        combined_draft = f"Subject: {current_subject}\n\n{current_body}"

        validation_col, save_col, download_col = st.columns(3)
        with validation_col:
            if st.button("Validate draft", key=f"validate_draft_{selected_invoice_number}"):
                st.session_state["last_email_validation"] = validate_email_draft(
                    selected_invoice_number,
                    str(selected_invoice["customer_name"]),
                    draft_recipient,
                    current_subject,
                    current_body,
                    human_review_required=True,
                )
                st.session_state["last_email_validation_invoice"] = selected_invoice_number
                st.session_state["last_email_validation_content"] = current_content

        if (
            st.session_state.get("last_email_validation_invoice") == selected_invoice_number
            and isinstance(st.session_state.get("last_email_validation"), dict)
        ):
            if st.session_state.get("last_email_validation_content") != current_content:
                st.info("The draft changed since it was last validated. Validate it again.")
            else:
                validation = st.session_state["last_email_validation"]
                if validation.get("valid"):
                    st.success(
                        "Draft validation passed. This checks format and required fields, "
                        "not mailbox ownership or factual correctness."
                    )
                else:
                    st.error("Draft validation failed.")
                    for message in validation.get("errors", []):
                        st.write(f"- {md_escape(message)}")
                for message in validation.get("warnings", []):
                    st.warning(message)

        with save_col:
            if st.button(
                "Save draft to database",
                type="primary",
                key=f"save_draft_{selected_invoice_number}",
                disabled=not _draft_review_ok,
            ):
                try:
                    saved = save_email_draft(
                        actor=_user,
                        invoice_number=selected_invoice_number,
                        customer_name=str(selected_invoice["customer_name"]),
                        recipient_email=draft_recipient,
                        subject=current_subject,
                        body=current_body,
                        tone=draft.get("tone", tone),
                        recommended_action=draft.get("recommended_action", ""),
                        human_review_required=True,
                    )
                    st.session_state["saved_email_draft_id"] = int(saved["id"])
                    st.session_state["saved_email_draft_invoice"] = selected_invoice_number
                    st.session_state["saved_email_draft_content"] = current_content
                    st.session_state["email_draft_saved"] = True
                    st.success(f"Draft #{saved['id']} saved to the database.")
                    log_event(
                        _user["username"],
                        _user["role"],
                        "email_draft_saved_ui",
                        "email_draft",
                        str(saved["id"]),
                        {"invoice_number": selected_invoice_number},
                    )
                except Exception as error:  # noqa: BLE001
                    st.error(f"Could not save draft: {type(error).__name__}: {error}")

        with download_col:
            st.download_button(
                label="Download edited email draft",
                data=combined_draft,
                file_name=f"payment_reminder_{selected_invoice_number}.txt",
                mime="text/plain",
                key=f"download_draft_{selected_invoice_number}",
                disabled=not _draft_review_ok,
            )

        saved_id = st.session_state.get("saved_email_draft_id")
        if saved_id and st.session_state.get("saved_email_draft_invoice") == selected_invoice_number:
            st.info(f"Database draft ID: {saved_id}. Status: saved draft; not sent.")
            _saved_matches_editor = st.session_state.get("saved_email_draft_content") == current_content
            if not _saved_matches_editor:
                st.warning(
                    "The text above changed after it was saved. Save the draft again before "
                    "submitting it for approval."
                )
            if st.button(
                "Submit saved draft for human approval",
                key=f"submit_draft_{saved_id}",
                disabled=not _saved_matches_editor,
            ):
                try:
                    submitted = submit_email_draft_for_approval(_user, int(saved_id))
                    log_event(
                        _user["username"],
                        _user["role"],
                        "email_draft_submitted_ui",
                        "email_draft",
                        str(saved_id),
                        {
                            "approval_request_id": submitted.get("approval_request_id"),
                            "invoice_number": selected_invoice_number,
                        },
                    )
                    flash_and_rerun(
                        "success",
                        f"Draft #{saved_id} submitted for human approval. "
                        f"Approval request ID: {submitted.get('approval_request_id')}. No email was sent.",
                    )
                except Exception as error:  # noqa: BLE001
                    st.error(f"Could not submit draft: {type(error).__name__}: {error}")

        st.warning(
            "This section only drafts email. Review the customer, invoice number, balance, due "
            "date, and wording before using the draft. Delivery happens only from an approved "
            "draft in the delivery section below."
        )


# ------------------------------------------------------------
# Saved drafts, approval and controlled delivery
# ------------------------------------------------------------
def render_saved_draft(draft_id: int, send_enabled: bool) -> None:
    """Show one saved draft with history, controlled send (approved only) and edit form."""
    saved = get_email_draft(_user, draft_id)
    recipient_on_file = str(saved.get("recipient_email", "")).strip()

    st.write(f"**To:** {md_escape(recipient_on_file)}")
    st.write(f"**Subject:** {md_escape(saved.get('subject'))}")
    st.text_area(
        "Saved body (read-only)",
        value=str(saved.get("body", "")),
        height=220,
        disabled=True,
        key=f"saved_body_readonly_{draft_id}",
    )
    st.write(f"**Status:** {md_escape(saved.get('status'))}")
    st.write(f"**Approval request:** {md_escape(saved.get('approval_request_id') or 'Not submitted')}")
    st.json(saved.get("validation", {}))

    st.subheader("Draft version history")
    try:
        versions = list_email_draft_versions(_user, draft_id)
        if versions:
            st.dataframe(
                pd.DataFrame(
                    [
                        {k: v for k, v in item.items() if k not in ("body", "content_hash")}
                        for item in versions
                    ]
                ),
                hide_index=True,
                **STRETCH,
            )
    except Exception as version_error:  # noqa: BLE001
        st.warning(f"Could not load version history: {type(version_error).__name__}: {version_error}")

    if saved.get("status") == "Approved":
        st.subheader("Controlled send")
        st.warning(
            "Sending is irreversible. Confirm the exact recipient and approved draft version "
            "before proceeding."
        )
        typed_recipient = st.text_input(
            "Type the exact recipient address to confirm", key=f"send_confirm_{draft_id}"
        )
        acknowledged = st.checkbox(
            "I verified the invoice, recipient, subject, body, and approval record.",
            key=f"send_ack_{draft_id}",
        )
        recipient_confirmed = bool(recipient_on_file) and typed_recipient.strip() == recipient_on_file
        if typed_recipient and not recipient_confirmed:
            st.caption("The typed address does not match the draft recipient.")
        if _user["role"] in ("admin", "reviewer"):
            if st.button(
                "Send approved email via Microsoft Graph",
                type="primary",
                key=f"send_approved_{draft_id}",
                disabled=not (send_enabled and acknowledged and recipient_confirmed),
            ):
                try:
                    send_result = send_approved_email(
                        _user,
                        draft_id,
                        expected_version=int(saved["version"]),
                        confirm_recipient=typed_recipient.strip(),
                    )
                    flash_and_rerun(
                        "success",
                        f"Email accepted by Microsoft Graph. Status: {send_result['status']}; "
                        f"attempt ID: {send_result['attempt_id']}.",
                    )
                except Exception as send_error:  # noqa: BLE001
                    st.error(
                        f"Email send did not complete normally: {type(send_error).__name__}: {send_error}. "
                        "Check send attempts before taking further action; do not retry an unknown outcome."
                    )
        else:
            st.warning("Only reviewers and administrators can send approved emails.")
        try:
            attempts = list_send_attempts(_user, draft_id)
            if attempts:
                st.caption("Send attempt history")
                st.dataframe(pd.DataFrame(attempts), hide_index=True, **STRETCH)
        except Exception as attempt_error:  # noqa: BLE001
            st.warning(f"Could not load send history: {type(attempt_error).__name__}: {attempt_error}")

    if saved.get("status") in ("Draft", "Rejected") and _user["role"] == "collector":
        tones = ["Professional", "Friendly", "Firm"]
        current_tone = saved.get("tone")
        with st.form(f"edit_email_draft_form_{draft_id}"):
            st.caption("Editing creates a new immutable version. Submitted or approved versions cannot be edited.")
            edit_to = st.text_input("Recipient", value=recipient_on_file, key=f"edit_to_{draft_id}")
            edit_subject = st.text_input(
                "Subject", value=str(saved.get("subject", "")), max_chars=200, key=f"edit_subject_{draft_id}"
            )
            edit_body = st.text_area(
                "Body", value=str(saved.get("body", "")), height=180, key=f"edit_body_{draft_id}"
            )
            edit_tone = st.selectbox(
                "Tone",
                tones,
                index=tones.index(current_tone) if current_tone in tones else 0,
                key=f"edit_tone_{draft_id}",
            )
            edit_reason = st.text_input(
                "Change reason", value="Updated after review", max_chars=250, key=f"edit_reason_{draft_id}"
            )
            save_edit = st.form_submit_button("Save as new draft version")
        if save_edit:
            try:
                updated = edit_email_draft(
                    _user,
                    draft_id,
                    expected_version=int(saved["version"]),
                    recipient_email=edit_to,
                    subject=edit_subject,
                    body=edit_body,
                    tone=edit_tone,
                    change_reason=edit_reason,
                )
                flash_and_rerun("success", f"Saved draft #{draft_id} as version {updated['version']}.")
            except Exception as edit_error:  # noqa: BLE001
                st.error(f"Draft edit failed: {type(edit_error).__name__}: {edit_error}")


st.divider()
st.header("V3.8 — Saved Drafts, Approval & Delivery")
try:
    _provider_status = provider_status()
except Exception as _provider_error:  # noqa: BLE001
    _provider_status = {
        "provider": "unknown",
        "mode": "unavailable",
        "enabled": False,
        "configured": False,
        "missing_environment_variables": [],
    }
    st.error(
        "Could not read the email provider status; sending is treated as disabled: "
        f"{type(_provider_error).__name__}: {_provider_error}"
    )
_send_enabled = bool(_provider_status.get("enabled") and _provider_status.get("configured"))

if _user["role"] in ("admin", "reviewer"):
    try:
        recover_stale_send_attempts(_user)
    except Exception as _send_recovery_error:  # noqa: BLE001
        st.warning(
            f"Send-attempt recovery needs review: {type(_send_recovery_error).__name__}: {_send_recovery_error}"
        )
if _send_enabled:
    st.warning(
        "LIVE EMAIL SENDING IS ENABLED. Every send requires an approved matching draft and "
        "explicit recipient confirmation."
    )
else:
    st.info(
        "Microsoft Graph email sending is disabled until the environment is configured. "
        "Draft creation and approval continue to work without sending."
    )
st.json(
    {
        "Provider": _provider_status.get("provider"),
        "Mode": _provider_status.get("mode"),
        "Configured": _provider_status.get("configured"),
        "Missing configuration": _provider_status.get("missing_environment_variables", []),
    }
)
st.caption(
    "Persisted draft history and approval status. Email can be sent only when the provider is "
    "enabled and configured, and only from an approved draft."
)
try:
    _draft_rows = list_email_drafts(_user, status="All", limit=200)
    if _draft_rows:
        _draft_table = pd.DataFrame(
            [
                {
                    "Draft ID": row.get("id"),
                    "Invoice": row.get("invoice_number"),
                    "Customer": row.get("customer_name"),
                    "Recipient": row.get("recipient_email"),
                    "Subject": row.get("subject"),
                    "Status": row.get("status"),
                    "Approval ID": row.get("approval_request_id"),
                    "Updated": row.get("updated_at"),
                    "Created by": row.get("created_by_username"),
                }
                for row in _draft_rows
            ]
        )
        st.dataframe(_draft_table, hide_index=True, **STRETCH)
        _draft_ids = [int(row["id"]) for row in _draft_rows]
        _selected_saved_draft_id = st.selectbox(
            "Inspect saved draft", _draft_ids, key="v37_inspect_draft_id"
        )
        with st.expander("View saved draft content", expanded=False):
            try:
                render_saved_draft(int(_selected_saved_draft_id), _send_enabled)
            except Exception as _draft_error:  # noqa: BLE001
                st.error(f"Could not load this draft: {type(_draft_error).__name__}: {_draft_error}")
    else:
        st.info("No saved email drafts yet. Generate a reminder and save it to begin.")
except Exception as _draft_library_error:  # noqa: BLE001
    st.error(f"Could not load saved drafts: {type(_draft_library_error).__name__}: {_draft_library_error}")

st.success("Invoice analysis completed successfully.")

st.caption(
    "Prototype only: invoice analysis and AI email drafting are enabled. "
    "Microsoft Graph sending is available only when explicitly enabled and approved; "
    "accounting-system integration is not enabled."
)


# ------------------------------------------------------------
# Customer response intelligence
# ------------------------------------------------------------
st.divider()
st.header("Customer Response Intelligence")
st.caption(
    "Classify a customer email or message and recommend the next collection action. "
    "This feature does not send emails or update accounting systems."
)

customer_response_text = st.text_area(
    "Paste the customer's response",
    placeholder="Example: We confirm that we will pay invoice INV-1001 on 30 October.",
    height=160,
    key="customer_response_text",
    help="Paste only the customer message. Avoid including unnecessary personal or confidential information.",
)

if st.button("Classify customer response", type="primary", key="classify_customer_response_button"):
    if not customer_response_text.strip():
        st.warning("Please paste a customer response before classifying it.")
    else:
        with st.spinner("Analysing customer response..."):
            try:
                classification_result = classify_customer_response(customer_response_text)
                st.session_state["latest_customer_response_classification"] = classification_result
                st.session_state["latest_classified_customer_response"] = customer_response_text.strip()
            except ResponseClassificationError as error:
                st.error(str(error))
            except Exception as error:  # noqa: BLE001
                # Show a useful but bounded error message; never expose API keys.
                st.error(
                    "An unexpected error occurred during classification. "
                    f"Error type: {type(error).__name__}. Details: {error}"
                )

classification = st.session_state.get("latest_customer_response_classification")
classified_text = st.session_state.get("latest_classified_customer_response")
if isinstance(classification, dict) and classified_text:
    st.subheader("Classification result")
    result_col1, result_col2 = st.columns(2)
    result_col1.metric("Response category", str(classification.get("category", "Unknown")))
    result_col2.metric("Sentiment", str(classification.get("sentiment", "Unknown")))
    st.write("**Recommended next action**")
    st.markdown(md_escape(classification.get("recommended_action", "")))

    if classification.get("requires_human_review") is True:
        st.warning("Human review is required before acting on this recommendation.")
    else:
        st.error("Safety check failed: human review must be required. Do not act on this result.")

    st.download_button(
        "Download classification as JSON",
        data=json.dumps(classification, indent=2, ensure_ascii=False, default=str),
        file_name="customer_response_classification.json",
        mime="application/json",
        key="download_customer_response_classification",
    )
    with st.expander("View the message that was classified"):
        st.text(classified_text)

st.caption(
    "AI output may be incorrect. Verify the original customer message and invoice records. "
    "This prototype requires human review and does not autonomously contact customers."
)


# ------------------------------------------------------------
# V2.5: Persistent collection follow-up manager and activity history
# ------------------------------------------------------------
st.divider()
st.header("Collection Follow-up Manager")
st.caption(
    "Create and track follow-up tasks in a local SQLite database. "
    "Tasks require human review; this feature does not send email or update an ERP."
)

with st.expander("Create a follow-up task", expanded=False):
    task_invoice_options = invoices["invoice_number"].astype(str).tolist()
    selected_task_invoice = st.selectbox(
        "Invoice for follow-up",
        options=task_invoice_options,
        key="follow_up_invoice_select",
    )
    selected_task_row = invoices.loc[
        invoices["invoice_number"].astype(str) == selected_task_invoice
    ].iloc[0]
    st.write(
        f"**Customer:** {md_escape(selected_task_row['customer_name'])}  \n"
        f"**Outstanding:** ₹{float(selected_task_row['outstanding_amount']):,.2f}  \n"
        f"**Priority:** {md_escape(selected_task_row['priority'])}  \n"
        f"**Dispute status:** {md_escape(selected_task_row['dispute_status'] or 'None recorded')}"
    )

    with st.form("create_follow_up_task_form"):
        task_type = st.selectbox("Task type", options=list(TASK_TYPES))
        task_description = st.text_area(
            "Task description",
            value="Review invoice and determine the appropriate next collection action.",
            max_chars=1000,
        )
        task_due_date = st.date_input("Follow-up date", value=date.today())
        task_status = st.selectbox("Initial status", options=["Open", "Awaiting approval"])
        task_create_submitted = st.form_submit_button("Create follow-up task", type="primary")

    if task_create_submitted:
        try:
            task_id = create_task(
                invoice_number=selected_task_invoice,
                customer_name=str(selected_task_row["customer_name"]),
                task_type=task_type,
                description=task_description,
                due_date=task_due_date,
                status=task_status,
                requires_human_review=True,
            )
            flash_and_rerun("success", f"Follow-up task #{task_id} created and saved to the local database.")
        except Exception as error:  # noqa: BLE001
            st.error(f"Could not create task: {type(error).__name__}: {error}")

st.subheader("Follow-up task queue")
status_filter = st.selectbox(
    "Filter tasks by status",
    options=["All", *TASK_STATUSES],
    key="follow_up_status_filter",
)
try:
    tasks = list_tasks(status_filter)
    if tasks:
        tasks_df = pd.DataFrame(tasks)
        tasks_df["requires_human_review"] = tasks_df["requires_human_review"].map(
            {1: "Yes", 0: "No"}
        )
        st.dataframe(
            tasks_df[
                [
                    "id",
                    "invoice_number",
                    "customer_name",
                    "task_type",
                    "description",
                    "due_date",
                    "status",
                    "requires_human_review",
                    "created_at",
                    "updated_at",
                ]
            ],
            hide_index=True,
            **STRETCH,
        )
        st.download_button(
            "Download follow-up tasks as CSV",
            data=csv_bytes(tasks_df),
            file_name="collection_follow_up_tasks.csv",
            mime="text/csv",
            key="download_follow_up_tasks_csv",
        )

        st.subheader("Update task status")
        task_ids = [int(task["id"]) for task in tasks]
        selected_task_id = st.selectbox(
            "Select task ID",
            options=task_ids,
            key="follow_up_update_task_id",
        )
        new_task_status = st.selectbox(
            "New status",
            options=list(TASK_STATUSES),
            key="follow_up_new_status",
        )
        status_notes = st.text_input(
            "Activity note (optional)",
            placeholder="For example: Reviewed evidence; awaiting manager approval.",
            key="follow_up_status_notes",
        )
        if st.button("Save task status", key="save_follow_up_status"):
            try:
                update_task_status(selected_task_id, new_task_status, status_notes)
                flash_and_rerun("success", f"Task #{selected_task_id} updated.")
            except Exception as error:  # noqa: BLE001
                st.error(f"Could not update task: {type(error).__name__}: {error}")
    else:
        st.info("No follow-up tasks match this filter. Create a task above to begin.")

    st.subheader("Collection activity history")
    activity = list_activity(limit=200)
    if activity:
        activity_df = pd.DataFrame(activity)
        st.dataframe(activity_df, hide_index=True, **STRETCH)
        st.download_button(
            "Download activity history as CSV",
            data=csv_bytes(activity_df),
            file_name="collection_activity_history.csv",
            mime="text/csv",
            key="download_collection_activity_csv",
        )
    else:
        st.info("No activity has been recorded yet.")
except Exception as error:  # noqa: BLE001
    st.error(
        "The follow-up manager could not load its local database. "
        f"Error type: {type(error).__name__}: {error}"
    )

st.caption(
    "V2.5 local persistence: tasks and activity are stored in collection_workflow.db "
    "beside app.py. Back up this file. Do not store sensitive customer data unless "
    "your storage and access controls are appropriate."
)


# ------------------------------------------------------------
# V2.6: Explainable, dispute-aware collection decision preview
# ------------------------------------------------------------
st.divider()
st.header("Collection Decision Assistant")
st.caption(
    "Explainable Python rules suggest a next step using invoice status and an optional "
    "customer-response category. This is a recommendation preview only."
)

decision_invoice_options = invoices["invoice_number"].astype(str).tolist()
decision_invoice_number = st.selectbox(
    "Select invoice for decision review",
    options=decision_invoice_options,
    key="decision_assistant_invoice",
)
decision_row = invoices.loc[invoices["invoice_number"].astype(str) == decision_invoice_number].iloc[0]

decision_categories = [
    "No response classified",
    "Payment promised",
    "Payment completed",
    "Invoice dispute",
    "Missing invoice",
    "Request for supporting documents",
    "Payment date requested",
    "No clear response",
]
decision_category = st.selectbox(
    "Customer response category (if known)",
    options=decision_categories,
    key="decision_assistant_response_category",
)
category_value = None if decision_category == "No response classified" else decision_category

try:
    decision = decide_collection_action(
        invoice_number=decision_invoice_number,
        outstanding_amount=float(decision_row["outstanding_amount"]),
        days_overdue=int(decision_row["days_overdue"]),
        dispute_status=decision_row["dispute_status"],
        customer_response_category=category_value,
    )
    result_left, result_right = st.columns(2)
    result_left.metric("Recommended workflow", decision.decision)
    result_right.metric("Reminder permitted by rules", "Yes" if decision.reminder_allowed else "No")
    st.write("**Recommended action**")
    st.markdown(md_escape(decision.recommended_action))
    st.write("**Reason**")
    st.markdown(md_escape(decision.reason))
    st.warning("Human review is required. This preview does not execute the recommendation.")
    st.download_button(
        "Download decision as JSON",
        data=json.dumps(
            {
                "invoice_number": decision.invoice_number,
                "decision": decision.decision,
                "recommended_action": decision.recommended_action,
                "reason": decision.reason,
                "requires_human_review": decision.requires_human_review,
                "reminder_allowed": decision.reminder_allowed,
            },
            indent=2,
            ensure_ascii=False,
        ),
        file_name=f"collection_decision_{decision_invoice_number}.json",
        mime="application/json",
        key="download_collection_decision_json",
    )
except Exception as error:  # noqa: BLE001
    st.error(f"Unable to evaluate collection rules: {type(error).__name__}: {error}")


# ------------------------------------------------------------
# V2.7: Human approval queue (records decisions; executes nothing)
# ------------------------------------------------------------
st.divider()
st.header("Human Approval Queue")
st.caption("Record review decisions for proposed collection actions. No email or accounting action is executed.")

with st.expander("Submit an action for approval", expanded=False):
    ap_invoice = st.selectbox(
        "Invoice for approval", invoices["invoice_number"].astype(str).tolist(), key="ap_invoice"
    )
    ap_action = st.selectbox(
        "Proposed action",
        [
            "Prepare payment reminder",
            "Review invoice dispute",
            "Verify reported payment",
            "Prepare invoice resend",
            "Prepare supporting documents",
            "Track payment promise",
            "Other",
        ],
        key="ap_action",
    )
    ap_rationale = st.text_area("Reason for proposed action", max_chars=1500, key="ap_rationale")
    st.caption(f"Requested by authenticated user: {md_escape(_user['username'])}")
    if st.button("Submit for approval", type="primary", key="ap_submit"):
        try:
            new_id = create_approval_request(_user, ap_invoice, ap_action, ap_rationale)
            log_event(
                _user["username"],
                _user["role"],
                "approval_request_created",
                "approval_request",
                str(new_id),
                {"invoice_number": ap_invoice, "action": ap_action},
            )
            flash_and_rerun("success", f"Approval request #{new_id} created.")
        except Exception as error:  # noqa: BLE001
            st.error(f"Could not create request: {type(error).__name__}: {error}")

ap_filter = st.selectbox("Approval status filter", ["All", *STATUSES], key="ap_filter")
try:
    ap_rows = list_authorized_approvals(_user, ap_filter)
    if ap_rows:
        ap_df = pd.DataFrame(ap_rows)
        st.dataframe(ap_df, hide_index=True, **STRETCH)
        pending = [r for r in ap_rows if r["status"] == "Pending"]
        if pending:
            st.subheader("Review pending request")
            if _user["role"] not in ("admin", "reviewer"):
                st.warning(
                    "Your role can view the queue but cannot approve or reject requests. "
                    "Contact an administrator for reviewer access."
                )
            else:
                ap_id = st.selectbox(
                    "Pending request ID", [int(r["id"]) for r in pending], key="ap_id"
                )
                ap_comments = st.text_area("Review comments", max_chars=1500, key="ap_comments")
                ap_decision = st.radio("Decision", ["Approved", "Rejected"], horizontal=True, key="ap_decision")
                if st.button("Record decision", key="ap_decide"):
                    try:
                        decide_approval_request(_user, ap_id, ap_decision, ap_comments)
                        try:
                            sync_email_draft_approval_status(_user, int(ap_id))
                        except Exception as _sync_error:  # noqa: BLE001
                            flash(
                                "warning",
                                "Approval was recorded, but email draft status synchronization needs "
                                f"recovery: {type(_sync_error).__name__}: {_sync_error}",
                            )
                        flash_and_rerun("success", f"Decision for request #{ap_id} recorded.")
                    except Exception as error:  # noqa: BLE001
                        log_event(
                            _user["username"],
                            _user["role"],
                            "approval_decision_failed",
                            "approval_request",
                            str(ap_id),
                            {"error_type": type(error).__name__},
                        )
                        st.error(f"Could not record request: {type(error).__name__}: {error}")
        st.download_button(
            "Download approval history as CSV",
            csv_bytes(ap_df),
            "human_approval_history.csv",
            "text/csv",
            key="ap_download",
        )
    else:
        st.info("No approval requests match this filter.")
except Exception as error:  # noqa: BLE001
    st.error(f"Could not load approval queue: {type(error).__name__}: {error}")


# ------------------------------------------------------------
# V2.8: Integrated collection workflow (classification -> decision -> task -> approval)
# ------------------------------------------------------------
st.divider()
st.header("V2.8 — Integrated Collection Workflow")
st.caption(
    "Connect a customer-response classification to the deterministic decision rules, "
    "a follow-up task, and a human approval request. This creates records only; it does not send email or update an ERP."
)

workflow_invoice = st.selectbox(
    "Invoice for integrated workflow",
    options=invoices["invoice_number"].astype(str).tolist(),
    key="v28_workflow_invoice",
)
workflow_row = invoices.loc[invoices["invoice_number"].astype(str) == workflow_invoice].iloc[0]
workflow_response = st.text_area(
    "Customer response (optional)",
    placeholder="Paste the customer's message if one exists. Leave blank to use invoice data only.",
    max_chars=12000,
    key="v28_workflow_response",
)
st.caption(f"Workflow requested by authenticated user: {md_escape(_user['username'])}")
workflow_due_date = st.date_input("Follow-up due date", value=date.today(), key="v28_workflow_due_date")
if st.button("Run integrated workflow", type="primary", key="v28_run_workflow"):
    try:
        workflow_classification = None
        workflow_category = None
        if workflow_response.strip():
            with st.spinner("Classifying customer response..."):
                workflow_classification = classify_customer_response(workflow_response.strip())
            workflow_category = workflow_classification["category"]

        workflow_decision = decide_collection_action(
            invoice_number=workflow_invoice,
            outstanding_amount=float(workflow_row["outstanding_amount"]),
            days_overdue=int(workflow_row["days_overdue"]),
            dispute_status=workflow_row["dispute_status"],
            customer_response_category=workflow_category,
        )

        action_map = {
            "Dispute review": "Review invoice dispute",
            "Payment verification": "Verify reported payment",
            "Invoice resend review": "Resend invoice",
            "Supporting documents": "Provide supporting documents",
            "Payment promise tracking": "Review payment promise",
            "Reminder draft eligible": "Prepare payment reminder",
            "Monitor": "Contact customer",
            "No collection required": "Other",
        }
        workflow_task_type = action_map.get(workflow_decision.decision, "Other")
        task_description_text = (
            f"V2.8 integrated workflow requested by {_user['username']}. "
            f"Decision: {workflow_decision.decision}. "
            f"Reason: {workflow_decision.reason} Recommended action: {workflow_decision.recommended_action}"
        )
        if workflow_classification:
            task_description_text += (
                f" Customer response category: {workflow_classification['category']}; "
                f"sentiment: {workflow_classification['sentiment']}."
            )
        # A proposed action is always routed to human review. Reminder eligibility is not sending permission.
        workflow_task_id = create_task(
            invoice_number=workflow_invoice,
            customer_name=str(workflow_row["customer_name"]),
            task_type=workflow_task_type,
            description=task_description_text[:1000],
            due_date=workflow_due_date,
            status="Awaiting approval",
            requires_human_review=True,
        )
        approval_action = action_map.get(workflow_decision.decision, "Other")
        approval_rationale = (
            f"Linked follow-up task ID: {workflow_task_id}. Decision: {workflow_decision.decision}. "
            f"Reason: {workflow_decision.reason} Recommended action: {workflow_decision.recommended_action} "
            f"Reminder permitted by deterministic rules: {workflow_decision.reminder_allowed}. "
            "Human review is mandatory; no external action is executed by this workflow."
        )
        workflow_approval_id = create_approval_request(
            _user, workflow_invoice, approval_action, approval_rationale, task_id=int(workflow_task_id)
        )
        st.session_state["v28_last_workflow_result"] = {
            "invoice_number": workflow_invoice,
            "classification": workflow_classification,
            "decision": {
                "decision": workflow_decision.decision,
                "recommended_action": workflow_decision.recommended_action,
                "reason": workflow_decision.reason,
                "reminder_allowed": workflow_decision.reminder_allowed,
                "requires_human_review": True,
            },
            "task_id": workflow_task_id,
            "approval_id": workflow_approval_id,
        }
        flash_and_rerun(
            "success",
            f"Workflow records created: task #{workflow_task_id}; approval request #{workflow_approval_id}.",
        )
    except Exception as error:  # noqa: BLE001
        st.error(
            "The integrated workflow could not be completed. Check the error and avoid "
            "re-submitting until you confirm whether any records were created. "
            f"Error type: {type(error).__name__}: {error}"
        )

last_workflow_result = st.session_state.get("v28_last_workflow_result")
if last_workflow_result:
    with st.expander("Latest integrated workflow result", expanded=True):
        st.json(last_workflow_result)
        st.info(
            "The task and approval request are separate persisted records linked by the task ID in the approval rationale. "
            "Reviewers must verify both records. The prototype does not automatically synchronize task status after approval."
        )

st.info(
    "V3.6 uses authenticated requester identity and service-layer approval checks for the V3.6 paths. "
    "Legacy direct database/service access remains outside this prototype’s security boundary."
)


# ------------------------------------------------------------
# V3.0: Bounded tool-using AI Collections Agent
# ------------------------------------------------------------
st.divider()
st.header("V3.0 — AI Collections Agent (Tool-Using Prototype)")
st.caption(
    "Runs a fixed, inspectable sequence of Python tools: retrieve invoice → optionally classify reply → "
    "apply deterministic rules → create follow-up task → submit for human approval."
)
st.info(
    "Safety boundary: the agent does not send emails, mark invoices paid, contact customers, "
    "or update ERP/accounting records. Classification uses Gemini only when a response is provided."
)
with st.form("v30_agent_form"):
    agent_invoice = st.selectbox(
        "Invoice to process", invoices["invoice_number"].astype(str).tolist(), key="v30_agent_invoice"
    )
    agent_response = st.text_area(
        "Customer reply (optional)",
        placeholder="Paste the customer reply to classify it, or leave blank for invoice-only rules.",
        max_chars=12000,
        key="v30_agent_response",
    )
    agent_requester = _user["username"]
    st.caption(f"Authenticated requester: {md_escape(agent_requester)}")
    agent_due_date = st.date_input("Follow-up due date", value=date.today(), key="v30_agent_due_date")
    agent_submitted = st.form_submit_button("Run agent workflow", type="primary")

if agent_submitted:
    if not agent_requester.strip():
        st.error("Requester name/team is required.")
    else:
        with st.spinner("Agent is running its bounded tool sequence..."):
            try:
                agent_run = run_collection_agent(
                    invoices=invoices,
                    invoice_number=agent_invoice,
                    customer_response=agent_response.strip() or None,
                    requested_by=agent_requester.strip(),
                    follow_up_date=agent_due_date,
                    actor=_user,
                )
                st.session_state["v30_agent_result"] = agent_run
                st.success(
                    f"Agent workflow completed. Follow-up task #{agent_run['follow_up_task_id']} and "
                    f"approval request #{agent_run['approval_request_id']} are awaiting human review."
                )
            except AgentWorkflowError as error:
                st.error(str(error))
            except Exception as error:  # noqa: BLE001
                st.error(
                    "Agent workflow stopped safely. No external action is enabled. "
                    f"Error type: {type(error).__name__}: {error}"
                )

agent_result = st.session_state.get("v30_agent_result")
if agent_result:
    with st.expander("Latest agent execution trace", expanded=True):
        try:
            trace_rows = [
                {"Step": 1, "Tool": "get_invoice_details", "Outcome": "Invoice retrieved from validated dataset"},
                {
                    "Step": 2,
                    "Tool": "classify_customer_reply",
                    "Outcome": "Skipped (no reply supplied)"
                    if not agent_result.get("classification")
                    else "Classification returned; human review required",
                },
                {"Step": 3, "Tool": "evaluate_collection_rules", "Outcome": agent_result["decision"]["decision"]},
                {
                    "Step": 4,
                    "Tool": "create_follow_up_task",
                    "Outcome": f"Task #{agent_result['follow_up_task_id']} created",
                },
                {
                    "Step": 5,
                    "Tool": "submit_human_approval",
                    "Outcome": f"Approval #{agent_result['approval_request_id']} created as Pending",
                },
            ]
            st.dataframe(pd.DataFrame(trace_rows), hide_index=True, **STRETCH)
            st.subheader("Structured agent result")
            st.json(agent_result)
            st.download_button(
                "Download agent execution trace as JSON",
                data=json.dumps(agent_result, indent=2, ensure_ascii=False, default=str),
                file_name=f"agent_execution_{agent_result['invoice']['invoice_number']}.json",
                mime="application/json",
                key="v30_agent_download",
            )
        except Exception as error:  # noqa: BLE001
            st.error(f"Could not display the agent trace: {type(error).__name__}: {error}")

st.warning(
    "Legacy workflow UI remains in this application. Do not expose this prototype to the public internet "
    "or use real customer data until each legacy action is verified against service-layer authorization "
    "and tenant isolation."
)


# ------------------------------------------------------------
# V3.1: Durable multi-step workflow orchestration
# ------------------------------------------------------------
st.divider()
st.header("V3.1 — Multi-step Workflow Orchestration")
st.caption(
    "A persisted, checkpointed workflow: retrieve invoice → classify response → evaluate rules → "
    "create follow-up task → submit approval. Failed workflows can resume from the last incomplete step."
)
st.warning(
    "Safety boundary: this prototype does not send emails, update ERP/accounting systems, or mark invoices paid. "
    "Workflow inputs, including an optional customer message, are stored in the local SQLite database to support resumption. "
    "Use synthetic or authorized data only."
)

with st.form("v31_create_workflow_form"):
    v31_invoice = st.selectbox(
        "Invoice for workflow", invoices["invoice_number"].astype(str).tolist(), key="v31_invoice"
    )
    v31_response = st.text_area(
        "Customer response (optional)",
        placeholder="Paste a customer response, or leave blank to use invoice data and deterministic rules only.",
        max_chars=12000,
        key="v31_response",
    )
    v31_requester = _user["username"]
    st.caption(f"Authenticated workflow owner: {md_escape(v31_requester)}")
    v31_due_date = st.date_input("Follow-up due date", value=date.today(), key="v31_due_date")
    v31_create = st.form_submit_button("Create and run workflow", type="primary")

if v31_create:
    if not v31_requester.strip():
        st.error("Requester name/team is required.")
    else:
        try:
            workflow_id = create_workflow(
                invoice_number=v31_invoice,
                requested_by=v31_requester.strip(),
                customer_response=v31_response.strip() or None,
                follow_up_date=v31_due_date,
            )
            st.session_state["v31_selected_workflow_id"] = workflow_id
            with st.spinner("Running workflow steps and saving checkpoints..."):
                v31_run_result = run_or_resume_workflow(workflow_id, invoices, actor=_user)
            st.session_state["v31_last_result"] = v31_run_result
            st.success(
                f"Workflow completed: {workflow_id}. Task #{v31_run_result['follow_up_task_id']}; "
                f"approval #{v31_run_result['approval_request_id']}."
            )
        except Exception as error:  # noqa: BLE001
            st.error(f"Workflow did not complete: {type(error).__name__}: {error}")

try:
    workflows_df = pd.DataFrame(list_workflows(limit=100))
except Exception as error:  # noqa: BLE001
    workflows_df = pd.DataFrame()
    st.error(f"Could not load workflow runs: {type(error).__name__}: {error}")

if not workflows_df.empty:
    st.subheader("Persisted workflow runs")
    st.dataframe(workflows_df, hide_index=True, **STRETCH)
    selectable_ids = workflows_df["workflow_id"].astype(str).tolist()
    default_id = st.session_state.get("v31_selected_workflow_id")
    default_index = selectable_ids.index(str(default_id)) if str(default_id) in selectable_ids else 0
    selected_workflow_id = st.selectbox(
        "Inspect or resume a workflow", selectable_ids, index=default_index, key="v31_inspect_workflow"
    )
    try:
        selected_workflow = get_workflow(selected_workflow_id)
        if selected_workflow:
            st.write(
                {
                    "Workflow ID": selected_workflow.get("workflow_id"),
                    "Invoice": selected_workflow.get("invoice_number"),
                    "Status": selected_workflow.get("status"),
                    "Current step": selected_workflow.get("current_step"),
                    "Last error": selected_workflow.get("error_message"),
                }
            )
            steps_df = pd.DataFrame(list_workflow_steps(selected_workflow_id))
            if not steps_df.empty:
                visible_cols = [
                    c
                    for c in ["step_name", "status", "error_message", "started_at", "completed_at"]
                    if c in steps_df.columns
                ]
                st.dataframe(steps_df[visible_cols], hide_index=True, **STRETCH)
            if selected_workflow.get("status") != "Completed":  # noqa: SIM102
                if st.button("Resume selected workflow", type="primary", key="v31_resume_workflow"):
                    with st.spinner("Resuming from saved checkpoints..."):
                        try:
                            resumed = run_or_resume_workflow(selected_workflow_id, invoices, actor=_user)
                            st.session_state["v31_last_result"] = resumed
                            flash_and_rerun(
                                "success",
                                "Workflow completed successfully. Existing completed steps were reused.",
                            )
                        except Exception as error:  # noqa: BLE001
                            st.error(f"Resume stopped safely: {type(error).__name__}: {error}")
            selected_workflow_result = selected_workflow.get("result")
            if selected_workflow_result:
                with st.expander("Completed workflow result", expanded=True):
                    st.json(selected_workflow_result)
                    st.download_button(
                        "Download workflow result JSON",
                        data=json.dumps(selected_workflow_result, indent=2, ensure_ascii=False, default=str),
                        file_name=f"workflow_{selected_workflow_id}.json",
                        mime="application/json",
                        key=f"v31_download_{selected_workflow_id}",
                    )
    except Exception as error:  # noqa: BLE001
        st.error(f"Could not load the selected workflow: {type(error).__name__}: {error}")
else:
    st.info("No V3.1 workflows have been created yet.")

st.info(
    "Recovery note: completed steps are checkpointed and reused on resume. The orchestrator also searches for "
    "workflow-marked task/approval records to recover from common interruption windows. As with any local prototype, "
    "test recovery carefully before production use; concurrent workers and hard process/database failures need stronger controls."
)


# ================= V3.2: EVALUATION & REGRESSION TESTING =================
st.divider()
st.header("V3.2 — Agent Evaluation & Regression Testing")
st.write(
    "Run the offline regression suite against deterministic collection rules and invoice retrieval. "
    "This test suite uses synthetic fixtures and does not call Gemini, create database records, or execute external actions."
)

if st.button("Run V3.2 regression suite", type="primary", key="v32_run_eval"):
    try:
        st.session_state["v32_evaluation_result"] = run_evaluation()
    except Exception as error:  # noqa: BLE001
        st.error(f"The regression suite could not run: {type(error).__name__}: {error}")

evaluation = st.session_state.get("v32_evaluation_result")
if evaluation:
    metric_cols = st.columns(4)
    metric_cols[0].metric("Overall status", evaluation["overall_status"])
    metric_cols[1].metric("Passed", evaluation["passed"])
    metric_cols[2].metric("Failed", evaluation["failed"])
    metric_cols[3].metric("Pass rate", f"{evaluation['pass_rate']}%")
    st.caption(evaluation["run_scope"])
    evaluation_df = pd.DataFrame(evaluation["results"])
    st.dataframe(evaluation_df, hide_index=True, **STRETCH)
    st.download_button(
        "Download regression results (CSV)",
        data=csv_bytes(evaluation_df, "utf-8-sig"),
        file_name="v3_2_regression_results.csv",
        mime="text/csv",
        key="v32_download_csv",
    )
    st.download_button(
        "Download evaluation report (JSON)",
        data=json.dumps(evaluation, indent=2, ensure_ascii=False, default=str),
        file_name="v3_2_evaluation_report.json",
        mime="application/json",
        key="v32_download_json",
    )
    if evaluation["overall_status"] == "PASS":
        st.success("All offline regression checks passed. This is not a production-readiness certification.")
    else:
        st.error("One or more regression checks failed. Review the rows marked FAIL or ERROR before continuing.")


# ================= V3.3: CONTROLLED PILOT READINESS =================
st.divider()
st.header("V3.3 — Controlled Pilot Readiness")
st.write(
    "Prepare a controlled, human-supervised pilot. This section benchmarks the live classifier on synthetic messages, "
    "tracks aggregate pilot metrics, and records readiness checks. It does not send emails or update accounting systems."
)

st.subheader("A. Synthetic customer-response classification benchmark")
st.warning(
    "Running this benchmark sends 21 synthetic example messages to your configured Gemini classifier and may incur API usage. "
    "It does not send customer messages. Review every mismatch; passing this small synthetic set is not proof of production accuracy."
)
_can_run_benchmark = _user["role"] == "admin"
if not _can_run_benchmark:
    st.info("Only administrators can run the live benchmark because it uses paid API calls.")
if st.button(
    "Run live classification benchmark",
    type="primary",
    key="v33_run_benchmark",
    disabled=not _can_run_benchmark,
):
    with st.spinner("Classifying synthetic examples..."):
        try:
            st.session_state["v33_benchmark"] = run_classification_benchmark()
            log_event(_user["username"], _user["role"], "classification_benchmark_run", "benchmark", "v33")
        except Exception as error:  # noqa: BLE001
            st.error(f"The benchmark could not run: {type(error).__name__}: {error}")

benchmark = st.session_state.get("v33_benchmark")
if benchmark:
    bcols = st.columns(4)
    bcols[0].metric("Cases", benchmark["total"])
    bcols[1].metric("Correct", benchmark["correct"])
    bcols[2].metric("Errors", benchmark["errors"])
    bcols[3].metric("Exact-match accuracy", f"{benchmark['accuracy_pct']}%")
    st.caption(benchmark["warning"])
    st.dataframe(
        pd.DataFrame(benchmark["by_category"]).T.reset_index().rename(columns={"index": "category"}),
        hide_index=True,
        **STRETCH,
    )
    benchmark_df = pd.DataFrame(benchmark["results"])
    st.dataframe(benchmark_df, hide_index=True, **STRETCH)
    st.download_button(
        "Download benchmark results (CSV)",
        csv_bytes(benchmark_df, "utf-8-sig"),
        "v3_3_classification_benchmark.csv",
        "text/csv",
        key="v33_benchmark_csv",
    )
    st.download_button(
        "Download benchmark report (JSON)",
        json.dumps(benchmark, indent=2, ensure_ascii=False, default=str),
        "v3_3_classification_benchmark.json",
        "application/json",
        key="v33_benchmark_json",
    )

st.divider()
st.subheader("B. Controlled-pilot readiness checklist")
st.write(
    "Check each control only after it has been verified in your actual environment. "
    "The checklist is a planning aid, not a security certification."
)
if "v33_readiness" not in st.session_state:
    st.session_state["v33_readiness"] = {key: False for key, _ in READINESS_CHECKLIST}
for key, label in READINESS_CHECKLIST:
    st.session_state["v33_readiness"][key] = st.checkbox(
        label, value=st.session_state["v33_readiness"].get(key, False), key=f"v33_check_{key}"
    )
readiness_total = len(READINESS_CHECKLIST)
readiness_count = sum(bool(v) for v in st.session_state["v33_readiness"].values())
st.progress(readiness_count / readiness_total if readiness_total else 0.0)
st.metric("Readiness controls verified", f"{readiness_count}/{readiness_total}")
if readiness_count < readiness_total:
    st.info("Do not begin a real-data pilot until all applicable controls are verified and approved by the business owner.")
else:
    st.success("All checklist items are marked complete. Obtain explicit business/security approval before using real data.")

st.divider()
st.subheader("C. Aggregate pilot metrics log")
st.caption(
    "Record only aggregate counts. Do not enter customer names, invoice numbers, message text, "
    "API keys, or other personal/confidential data."
)
with st.form("v33_pilot_metrics_form"):
    pilot_label = st.text_input("Pilot label", value="Internal synthetic pilot", max_chars=100)
    m1, m2, m3 = st.columns(3)
    sample_size = m1.number_input("Sample size", min_value=0, step=1, value=0)
    workflow_completed = m2.number_input("Workflows completed", min_value=0, step=1, value=0)
    workflow_failed = m3.number_input("Workflows failed", min_value=0, step=1, value=0)
    m4, m5, m6 = st.columns(3)
    recommendations_accepted = m4.number_input("Recommendations accepted", min_value=0, step=1, value=0)
    recommendations_corrected = m5.number_input("Recommendations corrected", min_value=0, step=1, value=0)
    avg_review_minutes = m6.number_input(
        "Average review minutes (0 if not measured)", min_value=0.0, step=0.5, value=0.0
    )
    pilot_notes = st.text_input("Short non-sensitive note (optional)", max_chars=500)
    pilot_submitted = st.form_submit_button("Save aggregate metrics")
if pilot_submitted:
    try:
        record_id = record_pilot_metrics(
            pilot_label=pilot_label,
            sample_size=int(sample_size),
            workflow_completed=int(workflow_completed),
            workflow_failed=int(workflow_failed),
            recommendations_accepted=int(recommendations_accepted),
            recommendations_corrected=int(recommendations_corrected),
            avg_review_minutes=float(avg_review_minutes) if avg_review_minutes > 0 else None,
            notes=pilot_notes,
        )
        st.success(f"Aggregate metrics saved. Record ID: {record_id}")
    except Exception as error:  # noqa: BLE001
        st.error(f"Metrics were not saved: {type(error).__name__}: {error}")

try:
    pilot_rows = list_pilot_metrics()
    if pilot_rows:
        kpis = calculate_pilot_kpis(pilot_rows)
        kcols = st.columns(4)
        kcols[0].metric("Total sample size", kpis["sample_size"])
        kcols[1].metric("Workflow completion", f"{kpis['completion_rate_pct']}%")
        kcols[2].metric("Workflow failure", f"{kpis['failure_rate_pct']}%")
        kcols[3].metric("Recommendation correction", f"{kpis['correction_rate_pct']}%")
        pilot_df = pd.DataFrame(pilot_rows)
        st.dataframe(pilot_df, hide_index=True, **STRETCH)
        st.download_button(
            "Download pilot metrics (CSV)",
            csv_bytes(pilot_df, "utf-8-sig"),
            "v3_3_pilot_metrics.csv",
            "text/csv",
            key="v33_pilot_csv",
        )
except Exception as error:  # noqa: BLE001
    st.error(f"Could not load pilot metrics: {type(error).__name__}: {error}")

st.info(
    "Pilot boundary: this prototype records aggregate metrics and readiness checklist state in the Streamlit session. "
    "Aggregate metric records persist in SQLite. The readiness checklist itself is not yet persisted or tied to authenticated users. "
    "Reviewer authentication, role-based access, robust audit trails, and formal retention controls remain prerequisites for production."
)


# ================= V3.4: PRODUCTION FOUNDATIONS =================
st.divider()
st.header("V3.4 — Production Foundations")
st.write("Local authentication, role-based UI permissions, audit events, and database operations for controlled testing.")

st.subheader("A. Account and role administration")
if _user["role"] == "admin":
    with st.form("v34_create_user_form"):
        new_username = st.text_input("New username", max_chars=80)
        new_display_name = st.text_input("Display name", max_chars=120)
        new_role = st.selectbox("Role", list(ROLES))
        new_password = st.text_input("Temporary password (minimum 12 characters)", type="password", max_chars=256)
        user_create_submitted = st.form_submit_button("Create user")
    if user_create_submitted:
        try:
            uid = create_user(new_username, new_password, new_role, new_display_name, created_by=_user["username"])
            log_event(
                _user["username"],
                _user["role"],
                "user_created",
                "user",
                str(uid),
                {"username": new_username, "role": new_role},
            )
            flash_and_rerun(
                "success",
                f"User created (ID {uid}). Share credentials securely and require a password change out of band.",
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"User was not created: {type(exc).__name__}: {exc}")
    users = list_users()
    if users:
        users_df = pd.DataFrame(users)
        st.dataframe(drop_sensitive_columns(users_df), hide_index=True, **STRETCH)
        active_users = [u for u in users if u["is_active"] and u["username"] != _user["username"]]
        if active_users:
            with st.form("v34_deactivate_user_form"):
                target = st.selectbox("Account to deactivate", [u["username"] for u in active_users])
                deactivate = st.form_submit_button("Deactivate account")
            if deactivate:
                try:
                    set_user_active(target, False, changed_by=_user["username"])
                    log_event(_user["username"], _user["role"], "user_deactivated", "user", target)
                    flash_and_rerun("success", f"Deactivated {target}.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not deactivate user: {type(exc).__name__}: {exc}")
else:
    st.info("Account administration is restricted to the administrator role.")

st.subheader("B. Audit trail")
st.caption("Audit trail is append-only through the application service; administrators can inspect and export recent events.")
if _user["role"] == "admin":
    audit_rows = list_events(limit=500)
    if audit_rows:
        audit_df = pd.DataFrame(audit_rows)
        st.dataframe(audit_df, hide_index=True, **STRETCH)
        st.download_button(
            "Export audit events (CSV)",
            csv_bytes(audit_df, "utf-8-sig"),
            "v3_4_audit_events.csv",
            "text/csv",
            key="v34_audit_export",
        )
    else:
        st.info("No audit events recorded yet.")
else:
    st.info("Audit trail viewing is restricted to the administrator role.")

st.subheader("C. Database health and backup")
if _user["role"] == "admin":
    db_info = get_database_info()
    st.json(db_info)
    if st.button("Run database integrity check", key="v34_db_integrity"):
        integrity_result = check_database_integrity()
        log_event(
            _user["username"],
            _user["role"],
            "database_integrity_check",
            "database",
            "collection_workflow.db",
            integrity_result,
        )
        if integrity_result["ok"]:
            st.success("SQLite integrity check passed.")
        else:
            st.error("Database integrity check failed. Stop writes and restore from a verified backup.")
        st.json(integrity_result)
    if st.button("Create database backup now", key="v34_db_backup"):
        try:
            backup_path = create_backup()
            log_event(_user["username"], _user["role"], "database_backup_created", "database_backup", backup_path.name)
            st.success(f"Backup created: {backup_path.name}")
            st.caption("Move backups to a protected location and test restoration periodically.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backup failed: {type(exc).__name__}: {exc}")
else:
    st.info("Database administration is restricted to the administrator role.")

st.warning(
    "Security status: local username/password authentication is not enterprise SSO/MFA. Before production, complete "
    "independent authorization review, HTTPS, managed secrets, tenant isolation, retention controls, backup-restore "
    "testing, dependency scanning, and deployment hardening."
)


# ================= V3.6: SECURE WORKFLOW AND AUDIT INTEGRATION =================
st.divider()
st.header("V3.6 — Secure Workflow Links & Security Boundaries")
st.caption(
    "Service-layer authorization, separation of duties, workflow/task/approval linkage, and auditable decisions. "
    "Email leaves the system only through the approval-gated Microsoft Graph path above."
)
try:
    _links = list_workflow_links(_user, limit=500)
    st.subheader("A. Workflow, task, and approval links")
    if _links:
        _links_df = pd.DataFrame(_links)
        st.dataframe(_links_df, hide_index=True, **STRETCH)
        st.download_button(
            "Export workflow links (CSV)",
            csv_bytes(_links_df, "utf-8-sig"),
            "v3_5_workflow_links.csv",
            "text/csv",
            key="v35_links_export",
        )
    else:
        st.info("No linked workflow records have been recorded yet. New V3.6 workflows will create links.")
except Exception as _e:  # noqa: BLE001
    st.error(f"Could not load workflow links: {type(_e).__name__}: {_e}")

st.subheader("B. V3.6 security boundaries")
st.markdown(
    "- Approval decisions are checked in the service layer, not only by hiding UI controls.\n"
    "- Requesters cannot approve or reject their own requests.\n"
    "- Workflow creation and resumption require an authenticated role.\n"
    "- Workflow, task, and approval IDs are linked when the V3.6 orchestration path creates them.\n"
    "- Approval decisions are recorded in the audit trail.\n"
    "- Email is sent only through the approval-gated Microsoft Graph path, and only when the provider is "
    "enabled and configured; payment settlement and ERP updates remain disabled."
)
st.warning(
    "Pre-production gate: direct database/process access, SSO/MFA, immutable or tamper-evident audit storage, "
    "and authorization checks for every legacy entry point still require verification before external production use."
)