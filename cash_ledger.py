import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

# ------------------------------------------------------------
# PAGE CONFIG
# ------------------------------------------------------------
st.set_page_config(
    page_title="Cash Dashboard",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ------------------------------------------------------------
# IST HELPERS
# ------------------------------------------------------------
def _ist_now():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).strftime("%H:%M:%S")

def _ist_today():
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist).date()

# ============================================================
# LOGIN
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("""
    <div style="text-align:center; padding: 40px 0;">
        <h1 style="color:#FFD700; font-size: 48px;">💰 Cash Dashboard</h1>
        <p style="color:#888; font-size: 16px;">Please log in to continue</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            u = st.text_input("Username", placeholder="Enter your username")
            p = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("🔐 Log In", use_container_width=True, type="primary")

        if submitted:
            try:
                correct_user = st.secrets.get("MY_USERNAME", "")
                correct_pass = st.secrets.get("MY_PASSWORD", "")
            except Exception:
                correct_user = ""
                correct_pass = ""

            if u and p and u == correct_user and p == correct_pass:
                st.session_state.authenticated = True
                st.session_state.logged_in_user = u
                st.rerun()
            else:
                st.error("❌ Invalid username or password")

    st.stop()

current_user = st.session_state.get("logged_in_user", "user")

# ============================================================
# GOOGLE SHEETS
# ============================================================
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)

@st.cache_resource
def get_spreadsheet():
    return get_gspread_client().open_by_key(st.secrets["spreadsheet_id"])

COMPANY_SHEET = "Cash_company"
DENOM_SHEET = "Cash_denomination"
PERSONAL_SHEET = "Cash_personal"

COMPANY_RANGE = "A1:E5000"
DENOM_RANGE = "A1:C20"
PERSONAL_RANGE = "A1:C5000"

COMPANY_HEADERS = ["Date", "Particulars", "Debit", "Credit", "Balance"]
DENOM_HEADERS = ["Denomination", "No of Notes", "Amount"]
PERSONAL_HEADERS = ["Date", "Particulars", "Amount"]

DENOM_VALUES = [0.1, 0.5, 1, 5, 10, 20, 50, "Online"]

# ------------------------------------------------------------
# READ / WRITE
# ------------------------------------------------------------
def _read_range(sheet_name, cell_range, headers):
    try:
        ws = get_spreadsheet().worksheet(sheet_name)
        rows = ws.get(cell_range)
        if not rows:
            return pd.DataFrame(columns=headers)
        first = rows[0]
        if first and [str(c).strip().lower() for c in first] == [h.lower() for h in headers]:
            data = rows[1:]
        else:
            data = rows
        n = len(headers)
        data = [(r + [""] * n)[:n] for r in data]
        df = pd.DataFrame(data, columns=headers)
        df = df.replace("", pd.NA).dropna(how="all").fillna("")
        return df
    except Exception as e:
        st.warning(f"Could not read {sheet_name}: {e}")
        return pd.DataFrame(columns=headers)

def _write_range(sheet_name, cell_range, headers, df):
    try:
        ws = get_spreadsheet().worksheet(sheet_name)
        values = [headers] + df.fillna("").astype(str).values.tolist()
        max_rows = 20 if sheet_name == DENOM_SHEET else 5000
        while len(values) < max_rows:
            values.append([""] * len(headers))
        ws.update(cell_range, values[:max_rows])
    except Exception as e:
        st.error(f"Could not write to {sheet_name}: {e}")

# ------------------------------------------------------------
# COMPANY (cached read)
# ------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
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

    # Sort by date before computing balance
    df['_sort_date'] = pd.to_datetime(df['Date'], errors='coerce', dayfirst=True)
    df = df.sort_values('_sort_date', na_position='last').reset_index(drop=True)

    # Recompute running balance
    balance = 0.0
    balances = []
    for _, row in df.iterrows():
        debit = float(row.get('Debit', 0) or 0)
        credit = float(row.get('Credit', 0) or 0)
        balance += credit - debit
        balances.append(round(balance, 3))
    df['Balance'] = balances

    df = df.drop(columns=['_sort_date'])
    _write_range(COMPANY_SHEET, COMPANY_RANGE, COMPANY_HEADERS, df)

    # Invalidate cache so next read gets fresh data
    st.cache_data.clear()

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
    df = load_company().drop(index=index).reset_index(drop=True)
    save_company(df)

# ------------------------------------------------------------
# DENOMINATION (cached read)
# ------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def load_denom():
    try:
        get_spreadsheet().worksheet(DENOM_SHEET)
    except Exception:
        st.error(f"⚠️ Could not find worksheet '{DENOM_SHEET}'. "
                 f"Please create a tab named exactly '{DENOM_SHEET}'.")
        return pd.DataFrame([
            {"Denomination": str(d), "No of Notes": 0.0, "Amount": 0.0}
            for d in DENOM_VALUES
        ])

    df = _read_range(DENOM_SHEET, DENOM_RANGE, DENOM_HEADERS)
    if df.empty or 'Denomination' not in df.columns:
        return pd.DataFrame([
            {"Denomination": str(d), "No of Notes": 0.0, "Amount": 0.0}
            for d in DENOM_VALUES
        ])

    df = df[df['Denomination'].astype(str).str.strip() != ""].copy()
    for col in ['No of Notes', 'Amount']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

    existing = set(df['Denomination'].astype(str))
    for d in DENOM_VALUES:
        if str(d) not in existing:
            df = pd.concat([df, pd.DataFrame([{
                "Denomination": str(d), "No of Notes": 0.0, "Amount": 0.0
            }])], ignore_index=True)

    return df

def save_denom(df):
    df = df.copy()

    def calc_amount(row):
        denom = str(row['Denomination']).strip()
        count = float(row['No of Notes'] or 0)
        if denom.lower() == "online":
            return count
        try:
            return float(denom) * count
        except Exception:
            return count

    df['Amount'] = df.apply(calc_amount, axis=1)
    _write_range(DENOM_SHEET, DENOM_RANGE, DENOM_HEADERS, df)

    # Invalidate cache
    st.cache_data.clear()

# ------------------------------------------------------------
# PERSONAL (cached read)
# ------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def load_personal():
    df = _read_range(PERSONAL_SHEET, PERSONAL_RANGE, PERSONAL_HEADERS)
    if df.empty:
        return df
    if 'Amount' in df.columns:
        df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce').fillna(0.0)
    return df

def save_personal(df):
    _write_range(PERSONAL_SHEET, PERSONAL_RANGE, PERSONAL_HEADERS, df)
    st.cache_data.clear()

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
    df = load_personal().drop(index=index).reset_index(drop=True)
    save_personal(df)

# ============================================================
# LOAD + COMPUTE TOTALS
# ============================================================
company_df = load_company()
personal_df = load_personal()
denom_df = load_denom()

company_balance = float(company_df['Balance'].iloc[-1]) if not company_df.empty else 0.0
personal_balance = float(personal_df['Amount'].sum()) if not personal_df.empty else 0.0

def compute_denom_total(df):
    physical = 0.0
    online = 0.0
    for _, row in df.iterrows():
        denom = str(row['Denomination']).strip()
        count = float(row['No of Notes'] or 0)
        if denom.lower() == "online":
            online = count
        else:
            try:
                physical += float(denom) * count
            except Exception:
                pass
    return physical, online

physical_only, online_amount = compute_denom_total(denom_df)
total_cash_physical = physical_only + online_amount

books_total = company_balance + personal_balance
difference = total_cash_physical - books_total

# ============================================================
# HEADER
# ============================================================
col_head1, col_head2, col_head3 = st.columns([3, 1, 1])
with col_head1:
    st.markdown(f"### 💰 Cash Dashboard — Welcome, **{current_user.title()}**")
    st.caption(f"🕐 Live · {_ist_now()} IST")
with col_head2:
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
with col_head3:
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()

st.divider()

# ============================================================
# HERO — TOTAL CASH
# ============================================================
st.markdown(f"""
<div style="background: linear-gradient(135deg, #232526 0%, #414345 100%);
            padding: 40px; border-radius: 20px; text-align: center;
            border: 2px solid #FFD700;">
    <p style="color: #aaa; font-size: 16px; margin: 0;">💵 TOTAL CASH AVAILABLE</p>
    <h1 style="color: #FFD700; font-size: 64px; margin: 10px 0;">
        ₹ {total_cash_physical:,.3f}
    </h1>
    <p style="color: #ccc; font-size: 14px; margin: 5px 0;">
        Physical: ₹{physical_only:,.3f} &nbsp;·&nbsp; Online: ₹{online_amount:,.3f}
    </p>
</div>
""", unsafe_allow_html=True)

st.markdown("")

# ============================================================
# THREE SUMMARY CARDS
# ============================================================
col_c1, col_c2, col_c3 = st.columns(3)

with col_c1:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                padding: 25px; border-radius: 15px; color: white;">
        <p style="margin: 0; font-size: 14px; opacity: 0.9;">🏢 COMPANY CASH</p>
        <h2 style="margin: 10px 0; font-size: 32px;">₹ {company_balance:,.3f}</h2>
        <p style="margin: 0; font-size: 12px; opacity: 0.8;">
            {len(company_df)} transactions
        </p>
    </div>
    """, unsafe_allow_html=True)

with col_c2:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
                padding: 25px; border-radius: 15px; color: white;">
        <p style="margin: 0; font-size: 14px; opacity: 0.9;">👤 PERSONAL CASH</p>
        <h2 style="margin: 10px 0; font-size: 32px;">₹ {personal_balance:,.3f}</h2>
        <p style="margin: 0; font-size: 12px; opacity: 0.8;">
            {len(personal_df)} entries
        </p>
    </div>
    """, unsafe_allow_html=True)

with col_c3:
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
                padding: 25px; border-radius: 15px; color: white;">
        <p style="margin: 0; font-size: 14px; opacity: 0.9;">📒 BOOKS TOTAL</p>
        <h2 style="margin: 10px 0; font-size: 32px;">₹ {books_total:,.3f}</h2>
        <p style="margin: 0; font-size: 12px; opacity: 0.8;">
            Company + Personal
        </p>
    </div>
    """, unsafe_allow_html=True)

st.markdown("")

# ============================================================
# RECONCILIATION BANNER
# ============================================================
if abs(difference) < 0.01:
    st.success(f"""
    ✅ **RECONCILED** — Physical cash matches the books perfectly.

    Physical Cash: **₹{total_cash_physical:,.3f}** = Books: **₹{books_total:,.3f}**
    """)
else:
    st.error(f"""
    ⚠️ **MISMATCH DETECTED** — Difference of **₹{difference:+,.3f}**

    | | Amount |
    |---|---|
    | Physical Cash | ₹{total_cash_physical:,.3f} |
    | Books (Company + Personal) | ₹{books_total:,.3f} |
    | **Difference** | **₹{difference:+,.3f}** |
    """)

st.divider()

# ============================================================
# QUICK ENTRY
# ============================================================
st.subheader("📝 Quick Entry")

entry_mode = st.radio(
    "What to record:",
    ["🏢 Company", "👤 Personal (+/−)", "🪙 Update Cash Count"],
    horizontal=True,
    label_visibility="collapsed"
)

# ---- COMPANY ENTRY ----
if entry_mode == "🏢 Company":
    with st.form("quick_company", clear_on_submit=True):
        col1, col2, col3, col4 = st.columns([1, 3, 1, 1])
        with col1:
            qc_date = st.date_input("Date", value=_ist_today(), key="qc_date")
        with col2:
            qc_part = st.text_input("Particulars", key="qc_part",
                                    placeholder="e.g., Ibrahim Advance")
        with col3:
            qc_debit = st.number_input("Debit (Out)", min_value=0.0, value=0.0,
                                       step=0.001, format="%.3f", key="qc_debit")
        with col4:
            qc_credit = st.number_input("Credit (In)", min_value=0.0, value=0.0,
                                        step=0.001, format="%.3f", key="qc_credit")

        if st.form_submit_button("➕ Add to Company", use_container_width=True, type="primary"):
            if qc_part and (qc_debit > 0 or qc_credit > 0):
                add_company(qc_date, qc_part, qc_debit, qc_credit)
                st.success(f"✅ Added: {qc_part} — Debit ₹{qc_debit:,.3f} / Credit ₹{qc_credit:,.3f}")
                st.rerun()
            else:
                st.error("Enter particulars and either debit or credit")

# ---- PERSONAL ENTRY ----
elif entry_mode == "👤 Personal (+/−)":
    col_in, col_out = st.columns([1, 1])

    with col_in:
        st.markdown("#### 📥 Money **IN** (income)")
        with st.form("quick_personal_in", clear_on_submit=True):
            pi_date = st.date_input("Date", value=_ist_today(), key="pi_date")
            pi_part = st.text_input("Particulars", key="pi_part",
                                    placeholder="e.g., Salary")
            pi_amount = st.number_input("Amount (₹)", min_value=0.0, value=0.0,
                                        step=0.001, format="%.3f", key="pi_amt")
            if st.form_submit_button("➕ Add Income", use_container_width=True, type="primary"):
                if pi_part and pi_amount > 0:
                    add_personal(pi_date, pi_part, pi_amount)
                    st.success(f"✅ Income added: {pi_part} +₹{pi_amount:,.3f}")
                    st.rerun()
                else:
                    st.error("Enter particulars and amount")

    with col_out:
        st.markdown("#### 📤 Money **OUT** (expense)")
        with st.form("quick_personal_out", clear_on_submit=True):
            po_date = st.date_input("Date", value=_ist_today(), key="po_date")
            po_part = st.text_input("Particulars", key="po_part",
                                    placeholder="e.g., Food, Electricity")
            po_amount = st.number_input("Amount (₹)", min_value=0.0, value=0.0,
                                        step=0.001, format="%.3f", key="po_amt")
            if st.form_submit_button("➖ Add Expense", use_container_width=True, type="primary"):
                if po_part and po_amount > 0:
                    add_personal(po_date, po_part, -po_amount)
                    st.success(f"✅ Expense added: {po_part} −₹{po_amount:,.3f}")
                    st.rerun()
                else:
                    st.error("Enter particulars and amount")

# ---- DENOMINATION UPDATE ----
else:
    st.markdown("#### 🪙 Update Physical Cash Count")
    st.caption("Edit any count — click **Save** to apply. Values are preserved between sessions.")

    if "denom_edit_buffer" not in st.session_state:
        st.session_state.denom_edit_buffer = denom_df[['Denomination', 'No of Notes']].copy()

    edited = st.data_editor(
        st.session_state.denom_edit_buffer,
        column_config={
            "Denomination": st.column_config.TextColumn("Denomination", disabled=True),
            "No of Notes": st.column_config.NumberColumn(
                "Count / Online (₹)",
                min_value=0.0, step=0.001, format="%.3f"
            )
        },
        hide_index=True,
        use_container_width=True,
        key="denom_edit_main"
    )

    live_physical = 0.0
    live_online = 0.0
    for _, row in edited.iterrows():
        denom = str(row['Denomination']).strip()
        cnt = float(row['No of Notes'] or 0)
        if denom.lower() == "online":
            live_online = cnt
        else:
            try:
                live_physical += float(denom) * cnt
            except Exception:
                pass
    live_total = live_physical + live_online

    col_lt1, col_lt2, col_lt3 = st.columns(3)
    with col_lt1:
        st.metric("🪙 Physical Cash (Live)", f"₹{live_physical:,.3f}")
    with col_lt2:
        st.metric("💳 Online (Live)", f"₹{live_online:,.3f}")
    with col_lt3:
        st.metric("💵 Total (Live)", f"₹{live_total:,.3f}")

    col_btn1, col_btn2 = st.columns([1, 1])
    with col_btn1:
        if st.button("💾 Save Cash Count", use_container_width=True, type="primary"):
            save_denom(edited)
            st.session_state.pop("denom_edit_buffer", None)
            st.success(f"✅ Saved — Total ₹{live_total:,.3f} at {_ist_now()} IST")
            st.rerun()

    with col_btn2:
        if st.button("↩️ Reset (Discard Changes)", use_container_width=True):
            st.session_state.pop("denom_edit_buffer", None)
            st.rerun()

st.divider()

# ============================================================
# RECENT ACTIVITY
# ============================================================
st.subheader("📜 Recent Activity")
st.caption("Last 10 transactions across all ledgers")

recent_rows = []

if not company_df.empty:
    for _, row in company_df.tail(10).iterrows():
        amount = float(row['Credit']) - float(row['Debit'])
        recent_rows.append({
            "Date": row['Date'],
            "Type": "🏢 Company",
            "Particulars": row['Particulars'],
            "Amount": amount,
            "Balance": float(row['Balance']) if row['Balance'] else 0.0,
        })

if not personal_df.empty:
    for _, row in personal_df.tail(10).iterrows():
        recent_rows.append({
            "Date": row['Date'],
            "Type": "👤 Personal",
            "Particulars": row['Particulars'],
            "Amount": float(row['Amount']),
            "Balance": None,
        })

if recent_rows:
    recent_df = pd.DataFrame(recent_rows)
    recent_df['Date'] = pd.to_datetime(recent_df['Date'], errors='coerce', dayfirst=True)
    recent_df = recent_df.sort_values('Date', ascending=False).head(10)

    st.dataframe(
        recent_df,
        column_config={
            "Amount": st.column_config.NumberColumn(format="₹%.3f"),
            "Balance": st.column_config.NumberColumn(format="₹%.3f"),
        },
        hide_index=True,
        use_container_width=True
    )
else:
    st.info("No transactions yet. Use Quick Entry above to add your first one.")

st.divider()

# ============================================================
# DETAILED VIEWS
# ============================================================
st.subheader("🔍 Detailed Views")

detail_tab = st.radio(
    "Choose a ledger",
    ["💼 Company Ledger", "👤 Personal Ledger", "🪙 Denomination Sheet", "📊 Charts"],
    horizontal=True,
    label_visibility="collapsed"
)

# ---- Company Ledger ----
if detail_tab == "💼 Company Ledger":
    if not company_df.empty:
        st.dataframe(
            company_df,
            column_config={
                "Debit": st.column_config.NumberColumn(format="₹%.3f"),
                "Credit": st.column_config.NumberColumn(format="₹%.3f"),
                "Balance": st.column_config.NumberColumn(format="₹%.3f"),
            },
            hide_index=True,
            use_container_width=True,
            height=400
        )

        with st.expander("🗑️ Delete a Company Transaction"):
            del_idx = st.selectbox(
                "Select row to delete",
                options=company_df.index.tolist(),
                format_func=lambda i: f"Row {i+1}: {company_df.loc[i, 'Date']} — {company_df.loc[i, 'Particulars']}",
                key="del_company"
            )
            if st.button("Delete", key="btn_del_company"):
                delete_company_row(del_idx)
                st.rerun()

        st.download_button(
            "📥 Download Company CSV",
            data=company_df.to_csv(index=False).encode('utf-8'),
            file_name=f"company_{_ist_today()}.csv",
            mime="text/csv"
        )
    else:
        st.info("No company transactions yet")

# ---- Personal Ledger ----
elif detail_tab == "👤 Personal Ledger":
    if not personal_df.empty:
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
                st.rerun()

        st.download_button(
            "📥 Download Personal CSV",
            data=personal_df.to_csv(index=False).encode('utf-8'),
            file_name=f"personal_{_ist_today()}.csv",
            mime="text/csv"
        )
    else:
        st.info("No personal entries yet")

# ---- Denomination Sheet ----
elif detail_tab == "🪙 Denomination Sheet":
    display_denom = denom_df.copy()
    display_denom['Amount'] = display_denom.apply(
        lambda r: float(r['No of Notes']) if str(r['Denomination']).lower() == 'online'
        else (float(r['Denomination']) * float(r['No of Notes'])
              if str(r['Denomination']).replace('.', '').isdigit() else 0.0),
        axis=1
    )
    st.dataframe(
        display_denom,
        column_config={
            "No of Notes": st.column_config.NumberColumn(format="%.3f"),
            "Amount": st.column_config.NumberColumn(format="₹%.3f"),
        },
        hide_index=True,
        use_container_width=True
    )
    st.caption(f"💵 Physical (excluding Online): ₹{physical_only:,.3f} · Online: ₹{online_amount:,.3f}")

# ---- Charts ----
else:
    col_ch1, col_ch2 = st.columns(2)

    with col_ch1:
        if not company_df.empty:
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(
                x=company_df['Date'], y=company_df['Balance'],
                mode='lines+markers', name='Company Balance',
                line=dict(color='#667eea', width=2)
            ))
            fig_c.update_layout(
                title="🏢 Company — Balance Trend",
                xaxis_title="Date", yaxis_title="Balance (₹)",
                height=400, margin=dict(l=10, r=10, t=50, b=10)
            )
            st.plotly_chart(fig_c, use_container_width=True,
                            config={'displayModeBar': False})
        else:
            st.info("No company data yet")

    with col_ch2:
        if not personal_df.empty:
            pdf = personal_df.copy()
            pdf['Cumulative'] = pdf['Amount'].cumsum()
            fig_p = go.Figure()
            fig_p.add_trace(go.Scatter(
                x=pdf['Date'], y=pdf['Cumulative'],
                mode='lines+markers', name='Personal Balance',
                line=dict(color='#f5576c', width=2)
            ))
            fig_p.update_layout(
                title="👤 Personal — Cumulative Balance",
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
st.caption("💰 Cash Dashboard · Built by S. Mohapatra · Data stored in Google Sheets")
