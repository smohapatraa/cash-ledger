import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
import hmac
import numpy as np
import gspread
from google.oauth2.service_account import Credentials

# ------------------------------------------------------------
# PAGE CONFIG
# ------------------------------------------------------------
st.set_page_config(
    page_title="Cash Ledger & Balance Tracker",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ------------------------------------------------------------
# IST TIME HELPERS
# ------------------------------------------------------------
def _ist_now():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%H:%M:%S")


def _ist_today():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).date()


# ------------------------------------------------------------
# LOGIN — MULTI-USER FROM SECRETS
# ------------------------------------------------------------
def check_password():
    def login_form():
        st.markdown("""
        <div style="text-align:center; padding: 30px 0;">
            <h1 style="color:#FFD700;">💰 Cash Ledger</h1>
            <p style="color:#888;">Please log in to continue</p>
        </div>
        """, unsafe_allow_html=True)

        with st.form("login_form"):
            st.text_input("Username", key="username_input")
            st.text_input("Password", type="password", key="password_input")
            st.form_submit_button("🔐 Log In", on_click=verify_login)

    def verify_login():
        try:
            users = st.secrets.get("credentials", {})
            entered_user = st.session_state.get("username_input", "")
            entered_pass = st.session_state.get("password_input", "")

            for key, creds in users.items():
                if isinstance(creds, dict):
                    if (creds.get("username") == entered_user
                            and hmac.compare_digest(entered_pass, creds.get("password", ""))):
                        st.session_state["authenticated"] = True
                        st.session_state["logged_in_user"] = entered_user
                        st.session_state["user_key"] = key
                        del st.session_state["password_input"]
                        return
            st.session_state["authenticated"] = False
        except Exception:
            st.session_state["authenticated"] = False

    if st.session_state.get("authenticated", False):
        return True

    login_form()
    if "authenticated" in st.session_state and not st.session_state["authenticated"]:
        st.error("😕 Invalid username or password")
    return False


if not check_password():
    st.stop()

current_user = st.session_state.get("logged_in_user", "user")


# ------------------------------------------------------------
# GOOGLE SHEETS CONNECTION (using gspread for range support)
# ------------------------------------------------------------
@st.cache_resource
def get_gspread_client():
    """Authenticate using service account credentials from secrets."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)


@st.cache_resource
def get_spreadsheet():
    """Get the spreadsheet by ID."""
    client = get_gspread_client()
    return client.open_by_key(st.secrets["spreadsheet_id"])


# ------------------------------------------------------------
# SHEET NAMES & RANGES
# ------------------------------------------------------------
COMPANY_SHEET = "Cash_company"
DENOM_SHEET = "Cash_denom"
PERSONAL_SHEET = "Cash_personal"

COMPANY_RANGE = "A1:E5000"
DENOM_RANGE = "A1:C20"
PERSONAL_RANGE = "A1:C5000"

COMPANY_HEADERS = ["Date", "Particulars", "Debit", "Credit", "Balance"]
DENOM_HEADERS = ["Denomination", "No of Notes", "Amount"]
PERSONAL_HEADERS = ["Date", "Particulars", "Amount"]

DENOM_VALUES = [0.1, 0.5, 1, 5, 10, 20, 50, "Online"]


# ------------------------------------------------------------
# READ / WRITE HELPERS (via gspread)
# ------------------------------------------------------------
def _read_range(sheet_name, cell_range, headers):
    """Read a specific range and return a DataFrame."""
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(sheet_name)
        rows = ws.get(cell_range)

        if not rows or len(rows) == 0:
            return pd.DataFrame(columns=headers)

        # First row should be headers — use it if matches
        first = rows[0]
        if first and [str(c).strip().lower() for c in first] == [h.lower() for h in headers]:
            data = rows[1:]
        else:
            data = rows

        # Ensure each row has the right number of columns
        n = len(headers)
        data = [(r + [""] * n)[:n] for r in data]
        df = pd.DataFrame(data, columns=headers)
        df = df.replace("", pd.NA).dropna(how="all").fillna("")
        return df
    except Exception as e:
        st.warning(f"Could not read {sheet_name}: {e}")
        return pd.DataFrame(columns=headers)


def _write_range(sheet_name, cell_range, headers, df):
    """Write a DataFrame back to a specific range."""
    try:
        ss = get_spreadsheet()
        ws = ss.worksheet(sheet_name)

        # Prepare data: headers + rows
        values = [headers] + df.fillna("").astype(str).values.tolist()

        # Expand to fill the range size
        max_rows = 5000
        if sheet_name == DENOM_SHEET:
            max_rows = 20

        while len(values) < max_rows:
            values.append([""] * len(headers))

        ws.update(cell_range, values[:max_rows])
    except Exception as e:
        st.error(f"Could not write to {sheet_name}: {e}")


# ------------------------------------------------------------
# COMPANY LEDGER
# ------------------------------------------------------------
def load_company():
    df = _read_range(COMPANY_SHEET, COMPANY_RANGE, COMPANY_HEADERS)
    if df.empty:
        return df
    for col in ['Debit', 'Credit', 'Balance']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    return df


def save_company(df):
    df = df.copy()
    # Recompute balances
    balance = 0.0
    balances = []
    for _, row in df.iterrows():
        debit = float(row.get('Debit', 0) or 0)
        credit = float(row.get('Credit', 0) or 0)
        balance += credit - debit
        balances.append(round(balance, 3))
    df['Balance'] = balances
    _write_range(COMPANY_SHEET, COMPANY_RANGE, COMPANY_HEADERS, df)


def add_company(date, particulars, debit, credit):
    df = load_company()
    new_row = pd.DataFrame([{
        "Date": str(date),
        "Particulars": particulars,
        "Debit": float(debit or 0),
        "Credit": float(credit or 0),
        "Balance": 0.0,
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    save_company(df)


def delete_company_row(index):
    df = load_company()
    df = df.drop(index=index).reset_index(drop=True)
    save_company(df)


# ------------------------------------------------------------
# DENOMINATION
# ------------------------------------------------------------
def load_denom():
    df = _read_range(DENOM_SHEET, DENOM_RANGE, DENOM_HEADERS)
    if df.empty:
        return pd.DataFrame([
            {"Denomination": str(d), "No of Notes": 0, "Amount": 0.0}
            for d in DENOM_VALUES
        ])
    for col in ['No of Notes', 'Amount']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    return df


def save_denom(df):
    df = df.copy()

    def calc_amount(row):
        try:
            d = float(row['Denomination'])
            return d * float(row['No of Notes'])
        except Exception:
            return float(row['No of Notes'])

    df['Amount'] = df.apply(calc_amount, axis=1)
    _write_range(DENOM_SHEET, DENOM_RANGE, DENOM_HEADERS, df)


# ------------------------------------------------------------
# PERSONAL
# ------------------------------------------------------------
def load_personal():
    df = _read_range(PERSONAL_SHEET, PERSONAL_RANGE, PERSONAL_HEADERS)
    if df.empty:
        return df
    if 'Amount' in df.columns:
        df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0.0)
    return df


def save_personal(df):
    _write_range(PERSONAL_SHEET, PERSONAL_RANGE, PERSONAL_HEADERS, df)


def add_personal(date, particulars, amount):
    df = load_personal()
    new_row = pd.DataFrame([{
        "Date": str(date),
        "Particulars": particulars,
        "Amount": float(amount or 0),
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    save_personal(df)


def delete_personal_row(index):
    df = load_personal()
    df = df.drop(index=index).reset_index(drop=True)
    save_personal(df)


# ============================================================
# MAIN UI
# ============================================================
st.title(f"💰 Cash Ledger — Welcome, {current_user.title()}")
st.caption(f"🕐 Live · {_ist_now()} IST")

col_refresh, col_logout = st.columns([1, 5])
with col_refresh:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()
with col_logout:
    if st.button("🚪 Logout"):
        st.session_state["authenticated"] = False
        st.rerun()

st.divider()

tab_company, tab_denom, tab_personal, tab_charts = st.tabs([
    "💼 Company", "🪙 Denomination", "👤 Personal", "📊 Charts"
])

# ============================================================
# TAB 1: COMPANY
# ============================================================
with tab_company:
    st.subheader("💼 Company Transactions")

    company_df = load_company()

    total_debit = company_df['Debit'].sum() if not company_df.empty else 0.0
    total_credit = company_df['Credit'].sum() if not company_df.empty else 0.0
    current_balance = company_df['Balance'].iloc[-1] if not company_df.empty else 0.0

    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        st.metric("📤 Total Debit", f"₹{total_debit:,.3f}")
    with col_c2:
        st.metric("📥 Total Credit", f"₹{total_credit:,.3f}")
    with col_c3:
        st.metric("💰 Current Balance", f"₹{current_balance:,.3f}")

    st.divider()

    with st.form("company_form", clear_on_submit=True):
        col_f1, col_f2, col_f3, col_f4 = st.columns([1, 3, 1, 1])
        with col_f1:
            c_date = st.date_input("Date", value=_ist_today(), key="c_date")
        with col_f2:
            c_particulars = st.text_input("Particulars", key="c_part")
        with col_f3:
            c_debit = st.number_input("Debit (Out)", min_value=0.0, value=0.0, step=0.001, format="%.3f", key="c_debit")
        with col_f4:
            c_credit = st.number_input("Credit (In)", min_value=0.0, value=0.0, step=0.001, format="%.3f", key="c_credit")

        submitted = st.form_submit_button("➕ Add Transaction", use_container_width=True, type="primary")
        if submitted:
            if c_particulars and (c_debit > 0 or c_credit > 0):
                add_company(c_date, c_particulars, c_debit, c_credit)
                st.success("✅ Transaction added")
                st.cache_resource.clear()
                st.rerun()
            else:
                st.error("Enter particulars and either debit or credit")

    if not company_df.empty:
        st.markdown("### 📜 Transaction History")
        display_df = company_df.copy()
        st.dataframe(
            display_df,
            column_config={
                "Debit": st.column_config.NumberColumn(format="₹%.3f"),
                "Credit": st.column_config.NumberColumn(format="₹%.3f"),
                "Balance": st.column_config.NumberColumn(format="₹%.3f"),
            },
            hide_index=True,
            use_container_width=True,
            height=400
        )

        with st.expander("🗑️ Delete a Transaction"):
            del_idx = st.selectbox(
                "Select row to delete",
                options=company_df.index.tolist(),
                format_func=lambda i: f"Row {i+1}: {company_df.loc[i, 'Date']} — {company_df.loc[i, 'Particulars']}",
                key="del_company"
            )
            if st.button("Delete", key="btn_del_company"):
                delete_company_row(del_idx)
                st.cache_resource.clear()
                st.rerun()

        st.download_button(
            "📥 Download Company CSV",
            data=company_df.to_csv(index=False).encode('utf-8'),
            file_name=f"company_{_ist_today()}.csv",
            mime="text/csv"
        )

# ============================================================
# TAB 2: DENOMINATION
# ============================================================
with tab_denom:
    st.subheader("🪙 Physical Cash Count")

    denom_df = load_denom()

    edited_denom = st.data_editor(
        denom_df[['Denomination', 'No of Notes']],
        column_config={
            "Denomination": st.column_config.TextColumn("Denomination", disabled=True),
            "No of Notes": st.column_config.NumberColumn("No. of Notes", min_value=0, step=1)
        },
        hide_index=True,
        use_container_width=True,
        key="denom_editor"
    )

    amounts = []
    for _, row in edited_denom.iterrows():
        denom = row['Denomination']
        count = row['No of Notes']
        try:
            amounts.append(float(denom) * float(count))
        except Exception:
            amounts.append(float(count))
    edited_denom['Amount'] = amounts

    total_cash = sum(amounts)

    col_d1, col_d2 = st.columns(2)
    with col_d1:
        st.metric("💵 Total Physical Cash", f"₹{total_cash:,.3f}")
    with col_d2:
        company_df2 = load_company()
        company_balance = company_df2['Balance'].iloc[-1] if not company_df2.empty else 0.0
        diff = total_cash - company_balance
        st.metric(
            "⚖️ Reconciliation",
            f"₹{diff:+,.3f}",
            delta="Balanced" if abs(diff) < 0.01 else "Mismatch",
            delta_color="normal" if abs(diff) < 0.01 else "inverse"
        )

    st.markdown("### 📋 Cash Breakdown")
    st.dataframe(
        edited_denom,
        column_config={
            "Amount": st.column_config.NumberColumn(format="₹%.3f"),
        },
        hide_index=True,
        use_container_width=True
    )

    if st.button("💾 Save Cash Count", type="primary"):
        save_denom(edited_denom)
        st.success("✅ Cash count saved")
        st.cache_resource.clear()
        st.rerun()

# ============================================================
# TAB 3: PERSONAL
# ============================================================
with tab_personal:
    st.subheader("👤 Personal Income & Expenses")

    personal_df = load_personal()

    total_income = personal_df[personal_df['Amount'] > 0]['Amount'].sum() if not personal_df.empty else 0.0
    total_expense = personal_df[personal_df['Amount'] < 0]['Amount'].sum() if not personal_df.empty else 0.0
    personal_balance = personal_df['Amount'].sum() if not personal_df.empty else 0.0

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.metric("📥 Income", f"₹{total_income:,.3f}")
    with col_p2:
        st.metric("📤 Expense", f"₹{abs(total_expense):,.3f}")
    with col_p3:
        st.metric("💰 Balance", f"₹{personal_balance:,.3f}")

    st.divider()

    with st.form("personal_form", clear_on_submit=True):
        col_pf1, col_pf2, col_pf3 = st.columns([1, 3, 1])
        with col_pf1:
            p_date = st.date_input("Date", value=_ist_today(), key="p_date")
        with col_pf2:
            p_particulars = st.text_input("Particulars", key="p_part")
        with col_pf3:
            p_amount = st.number_input("Amount (+ in / − out)", value=0.0, step=0.001, format="%.3f", key="p_amt")

        submitted = st.form_submit_button("➕ Add Entry", use_container_width=True, type="primary")
        if submitted:
            if p_particulars and p_amount != 0:
                add_personal(p_date, p_particulars, p_amount)
                st.success("✅ Entry added")
                st.cache_resource.clear()
                st.rerun()
            else:
                st.error("Enter particulars and amount")

    if not personal_df.empty:
        st.markdown("### 📜 Personal History")
        st.dataframe(
            personal_df,
            column_config={
                "Amount": st.column_config.NumberColumn(format="₹%.3f"),
            },
            hide_index=True,
            use_container_width=True,
            height=400
        )

        with st.expander("🗑️ Delete a Personal Entry"):
            del_pidx = st.selectbox(
                "Select row to delete",
                options=personal_df.index.tolist(),
                format_func=lambda i: f"Row {i+1}: {personal_df.loc[i, 'Date']} — {personal_df.loc[i, 'Particulars']}",
                key="del_personal"
            )
            if st.button("Delete", key="btn_del_personal"):
                delete_personal_row(del_pidx)
                st.cache_resource.clear()
                st.rerun()

        st.download_button(
            "📥 Download Personal CSV",
            data=personal_df.to_csv(index=False).encode('utf-8'),
            file_name=f"personal_{_ist_today()}.csv",
            mime="text/csv"
        )

# ============================================================
# TAB 4: CHARTS
# ============================================================
with tab_charts:
    st.subheader("📊 Charts & Insights")

    company_df3 = load_company()
    personal_df3 = load_personal()

    col_ch1, col_ch2 = st.columns(2)

    with col_ch1:
        if not company_df3.empty:
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(
                x=company_df3['Date'], y=company_df3['Balance'],
                mode='lines+markers', name='Company Balance',
                line=dict(color='#00ff88', width=2)
            ))
            fig_c.update_layout(
                title="Company — Balance Trend",
                xaxis_title="Date", yaxis_title="Balance (₹)",
                height=400, margin=dict(l=10, r=10, t=50, b=10)
            )
            st.plotly_chart(fig_c, use_container_width=True,
                            config={'displayModeBar': False})
        else:
            st.info("No company data yet")

    with col_ch2:
        if not personal_df3.empty:
            personal_df3 = personal_df3.copy()
            personal_df3['Cumulative'] = personal_df3['Amount'].cumsum()
            fig_p = go.Figure()
            fig_p.add_trace(go.Scatter(
                x=personal_df3['Date'], y=personal_df3['Cumulative'],
                mode='lines+markers', name='Personal Balance',
                line=dict(color='#4facfe', width=2)
            ))
            fig_p.update_layout(
                title="Personal — Cumulative Balance",
                xaxis_title="Date", yaxis_title="Balance (₹)",
                height=400, margin=dict(l=10, r=10, t=50, b=10)
            )
            st.plotly_chart(fig_p, use_container_width=True,
                            config={'displayModeBar': False})
        else:
            st.info("No personal data yet")

# ------------------------------------------------------------
# FOOTER
# ------------------------------------------------------------
st.divider()
st.caption("💰 Cash Ledger · Built by S. Mohapatra · Data stored in Google Sheets")
