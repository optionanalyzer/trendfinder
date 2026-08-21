import streamlit as st
import pandas as pd
import requests
import urllib.parse
from datetime import datetime
import pytz
import json
import os
from streamlit_autorefresh import st_autorefresh
import plotly.graph_objects as go
import sqlite3
import uuid
import hashlib

# ===================================================================
# ⚠️ HARDCODE YOUR ACCESS TOKEN HERE 
# ===================================================================
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIzTUJDMzIiLCJqdGkiOiI2YTg3YmY1OWFjYjQyYjdjNmY2ZTVlMmMiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlhdCI6MTc4NzI4MTI0MSwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxNzg3MzQ5NjAwfQ.BOKOMQ8-hGYvesEuuK7oETVxN8cmUtaomjssXVuM9ns" 


# ===================================================================
# 0. DATABASE & SINGLE-SESSION AUTHENTICATION ENGINE
# ===================================================================
def init_db():
    """Initializes a local SQLite database for user management."""
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, active_session TEXT)''')
    
    # Create a default user (Username: admin | Password: admin123)
    default_pw = hashlib.sha256('admin123'.encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (username, password, active_session) VALUES (?, ?, ?)", 
              ('admin', default_pw, None))
    conn.commit()
    conn.close()

init_db()

# Initialize local session variables
if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'username' not in st.session_state:
    st.session_state.username = None

def verify_session():
    """Checks if the current browser tab holds the active DB lock."""
    if not st.session_state.session_id or not st.session_state.username:
        return False
    
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute("SELECT active_session FROM users WHERE username=?", (st.session_state.username,))
    row = c.fetchone()
    conn.close()
    
    # If the DB session matches our local session, we are authorized
    if row and row[0] == st.session_state.session_id:
        return True
    return False

def logout():
    """Clears the DB lock and local session."""
    if st.session_state.username:
        conn = sqlite3.connect('fno_users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET active_session = NULL WHERE username=?", (st.session_state.username,))
        conn.commit()
        conn.close()
    st.session_state.session_id = None
    st.session_state.username = None
    st.rerun()

# --- THE LOGIN SCREEN ---
if not verify_session():
    # We must call page config here if not authenticated, as it must be the first Streamlit command
    st.set_page_config(page_title="Login - FnO Terminal", layout="centered")
    
    st.markdown("<h2 style='text-align: center;'>🔐 FnO Intelligence Terminal</h2>", unsafe_allow_html=True)
    st.markdown("---")
    
    with st.form("login_form"):
        st.write("Please log in to access the dashboard.")
        input_user = st.text_input("Username")
        input_pass = st.text_input("Password", type="password")
        force_login = st.checkbox("Force terminate other active sessions")
        
        submit = st.form_submit_button("Login to Terminal")
        
        if submit:
            hashed_pass = hashlib.sha256(input_pass.encode()).hexdigest()
            conn = sqlite3.connect('fno_users.db')
            c = conn.cursor()
            c.execute("SELECT password, active_session FROM users WHERE username=?", (input_user,))
            user_data = c.fetchone()
            
            if user_data:
                db_pass, db_session = user_data
                if db_pass == hashed_pass:
                    if db_session is not None and not force_login:
                        st.error("⚠️ You are already logged in on another device/browser. Check the box above to terminate it.")
                    else:
                        # Grant access and lock the session
                        new_session_id = str(uuid.uuid4())
                        c.execute("UPDATE users SET active_session=? WHERE username=?", (new_session_id, input_user))
                        conn.commit()
                        st.session_state.session_id = new_session_id
                        st.session_state.username = input_user
                        st.success("Login successful! Redirecting...")
                        st.rerun()
                else:
                    st.error("Invalid password.")
            else:
                st.error("Invalid username.")
            conn.close()
            
    # Stop the rest of the app from running if not logged in
    st.stop()

# ===================================================================
# END AUTHENTICATION - MAIN APP CONTINUES BELOW
# ===================================================================

# ===================================================================
# 📲 TELEGRAM ALERT CONFIGURATION
# ===================================================================
TELEGRAM_BOT_TOKEN = "8968266056:AAFlTouDWGZQInTpp3SFEZINw3Nj8YL5cxI"
TELEGRAM_CHAT_ID = "-5311750328"

def send_telegram_alert(message):
    """Fires a Telegram message asynchronously to prevent Streamlit UI lag."""
    if TELEGRAM_BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        requests.post(url, json=payload, timeout=2) 
    except Exception:
        pass

# ===================================================================
# 🚀 INITIALIZATION ALERT (SERVER-LEVEL CACHE)
# ===================================================================
@st.cache_resource
def notify_server_start():
    send_telegram_alert("🚀 *FnO Positional Terminal Started Successfully*")
    return True

# This will only execute once per server boot
notify_server_start()
        
# -------------------------------------------------------------------
# 0. PAGE CONFIGURATION & AUTO REFRESH
# -------------------------------------------------------------------
st.set_page_config(page_title="FnO Intelligence Terminal", layout="wide")

# Run a seamless background refresh every 15 seconds
st_autorefresh(interval=15000, limit=None, key="fno_terminal_refresh")

# -------------------------------------------------------------------
# 1. FETCH AND PROCESS UPSTOX INSTRUMENT DATA
# -------------------------------------------------------------------
@st.cache_data(show_spinner="Fetching Master Instrument List from Upstox...")
def load_instruments():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    df = pd.read_csv(url)
    
    fno_df = df[
        df['instrument_key'].str.startswith('NSE_FO|') | 
        df['instrument_key'].str.startswith('NSE_INDEX|') |
        df['instrument_key'].str.startswith('BSE_FO|') |
        df['instrument_key'].str.startswith('BSE_INDEX|')
    ]
    
    unique_symbols = fno_df['name'].dropna().unique().tolist()
    unique_symbols.sort()
    
    return df, fno_df, unique_symbols

master_df, fno_df, fno_symbols = load_instruments()

# -------------------------------------------------------------------
# 2. CORE API DATA FETCHERS
# -------------------------------------------------------------------
def get_expiries_for_symbol(symbol, df):
    symbol_data = df[df['name'] == symbol]
    expiries = symbol_data['expiry'].dropna().unique().tolist()
    return sorted(expiries)

def get_underlying_ltp(instrument_key, access_token):
    safe_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', {})
            if data:
                return list(data.values())[0].get('last_price')
    except Exception:
        pass
    return None

def fetch_live_vix(access_token):
    if not access_token or access_token == "YOUR_UPSTOX_ACCESS_TOKEN_HERE":
        return "N/A"
        
    safe_key = urllib.parse.quote("NSE_INDEX|India VIX")
    url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json().get('data', {})
            if data:
                ltp = list(data.values())[0].get('last_price')
                if ltp is not None:
                    return str(ltp)
        return f"Err: {response.status_code}"
    except Exception:
        return "N/A"

def get_option_chain_data(instrument_key, expiry_date, access_token, spot_price):
    if not access_token or access_token == "YOUR_UPSTOX_ACCESS_TOKEN_HERE" or not spot_price:
        return None, None

    safe_key = urllib.parse.quote(instrument_key)
    url = f"https://api.upstox.com/v2/option/chain?instrument_key={safe_key}&expiry_date={expiry_date}"
    headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return None, None
        
        data = response.json().get('data', [])
        if not data:
            return None, None

        chain_df = pd.json_normalize(data)
        chain_df.fillna(0, inplace=True)
        
        chain_df['distance_from_spot'] = abs(chain_df['strike_price'] - spot_price)
        atm_index = chain_df['distance_from_spot'].idxmin()
        atm_strike = chain_df.loc[atm_index, 'strike_price']

        return chain_df, atm_strike
    except Exception:
        return None, None

# -------------------------------------------------------------------
# 3. TERMINAL HEADER USER INTERFACE
# -------------------------------------------------------------------
st.markdown("### 📊 FnO Intelligence Terminal (Positional)")
st.markdown("---")

col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1, 1, 1])

with col1:
    selected_symbol = st.selectbox("Select Instrument", options=fno_symbols, index=None, placeholder="Search for an instrument...", label_visibility="collapsed")
with col2:
    available_expiries = get_expiries_for_symbol(selected_symbol, fno_df) if selected_symbol else []
    selected_expiry = st.selectbox("Select Expiry", options=available_expiries if available_expiries else ["No Expiry Found"], label_visibility="collapsed")

INDEX_MAP = {
    "NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY": "NSE_INDEX|Nifty Fin Service", "MIDCPNIFTY": "NSE_INDEX|NIFTY MID SELECT",
    "SENSEX": "BSE_INDEX|SENSEX", "BANKEX": "BSE_INDEX|BANKEX"
}

if selected_symbol in INDEX_MAP: target_instrument_key = INDEX_MAP[selected_symbol]
elif selected_symbol:
    eq_rows = master_df[(master_df['name'] == selected_symbol) & (master_df['instrument_key'].str.startswith('NSE_EQ|'))]
    target_instrument_key = eq_rows['instrument_key'].iloc[0] if not eq_rows.empty else ""
else: target_instrument_key = ""

# -------------------------------------------------------------------
# 4. EXECUTE DATA STREAMS (MACRO vs MICRO)
# -------------------------------------------------------------------
live_vix = fetch_live_vix(ACCESS_TOKEN)
underlying_spot = get_underlying_ltp(target_instrument_key, ACCESS_TOKEN) if target_instrument_key else None
chain_df, atm_strike = get_option_chain_data(target_instrument_key, selected_expiry, ACCESS_TOKEN, underlying_spot) if available_expiries and target_instrument_key else (None, None)

live_pcr = None
micro_pcr = None
active_strikes_df = pd.DataFrame()

if chain_df is not None:
    atm_idx = chain_df['distance_from_spot'].idxmin()
    
    # MACRO PCR (ATM ± 5) - For overarching daily trend
    active_strikes_df = chain_df.iloc[max(0, atm_idx - 5):min(len(chain_df) - 1, atm_idx + 5) + 1].copy()
    total_call_oi = active_strikes_df.get('call_options.market_data.oi', pd.Series([0])).sum()
    total_put_oi = active_strikes_df.get('put_options.market_data.oi', pd.Series([0])).sum()
    live_pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 99.9

    # MICRO PCR (ATM ± 2) - For instant momentum
    micro_strikes_df = chain_df.iloc[max(0, atm_idx - 2):min(len(chain_df) - 1, atm_idx + 2) + 1].copy()
    micro_call_oi = micro_strikes_df.get('call_options.market_data.oi', pd.Series([0])).sum()
    micro_put_oi = micro_strikes_df.get('put_options.market_data.oi', pd.Series([0])).sum()
    micro_pcr = round(micro_put_oi / micro_call_oi, 2) if micro_call_oi > 0 else 99.9

with col3:
    if live_pcr is not None:
        pcr_color = "normal" if live_pcr >= 1 else "inverse"
        st.metric(label="MACRO PCR (±5)", value=live_pcr, delta=f"ATM: {int(atm_strike)}", delta_color=pcr_color)
    else:
        st.metric(label="MACRO PCR (±5)", value="---", delta="No Data", delta_color="off")

with col4:
    if micro_pcr is not None:
        mpcr_color = "normal" if micro_pcr >= 1 else "inverse"
        st.metric(label="MICRO PCR (±2)", value=micro_pcr, delta="Hyper-Local", delta_color=mpcr_color)
    else:
        st.metric(label="MICRO PCR (±2)", value="---", delta="No Data", delta_color="off")

with col5:
    st.metric(label="INDIA VIX", value=live_vix)

# -------------------------------------------------------------------
# 5. CUMULATIVE DAILY OI TRACKER (Persistent Positional Engine)
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    state_prefix = f"{target_instrument_key}_{selected_expiry}"
    ist_tz = pytz.timezone('Asia/Kolkata')
    current_date_str = datetime.now(ist_tz).strftime('%Y-%m-%d')
    
    # Create a safe, unique filename for the selected instrument/expiry
    safe_prefix = state_prefix.replace("|", "_").replace(" ", "_")
    baseline_file = f"oi_baseline_{safe_prefix}.json"
    
    # 1. Function to load baseline from local disk
    def load_local_baseline():
        if os.path.exists(baseline_file):
            try:
                with open(baseline_file, 'r') as f:
                    saved_data = json.load(f)
                    # Only recover if the saved data belongs to TODAY
                    if saved_data.get('date') == current_date_str:
                        return {float(k): v for k, v in saved_data.get('baseline', {}).items()}
            except Exception:
                pass
        return {}

    # 2. Function to save baseline to local disk
    def save_local_baseline(baseline_data):
        try:
            payload = {
                'date': current_date_str,
                'baseline': baseline_data
            }
            with open(baseline_file, 'w') as f:
                json.dump(payload, f)
        except Exception:
            pass

    # 3. Initialize Memory
    if st.session_state.get('oi_instrument_tracker') != state_prefix:
        st.session_state.daily_baseline = load_local_baseline()
        st.session_state.oi_instrument_tracker = state_prefix
        
    # 4. Establish & Save the Institutional Baseline 
    if not st.session_state.daily_baseline:
        new_baseline = {}
        for index, row in active_strikes_df.iterrows():
            strike = float(row['strike_price'])
            new_baseline[strike] = {
                'call_oi': row.get('call_options.market_data.oi', 0),
                'put_oi': row.get('put_options.market_data.oi', 0)
            }
        st.session_state.daily_baseline = new_baseline
        save_local_baseline(new_baseline)
        st.info("🎯 Daily Institutional Baseline Established & Saved to Disk.")
    elif 'baseline_recovered_msg' not in st.session_state:
        st.success("✅ Recovered Today's Institutional Baseline from Disk.")
        st.session_state.baseline_recovered_msg = True

    call_chg_list = []
    put_chg_list = []

    # 5. Calculate Cumulative Daily Shift
    for index, row in active_strikes_df.iterrows():
        strike = float(row['strike_price'])
        
        curr_call_oi = row.get('call_options.market_data.oi', 0)
        curr_put_oi = row.get('put_options.market_data.oi', 0)
        
        base_call_oi = st.session_state.daily_baseline.get(strike, {}).get('call_oi', curr_call_oi)
        base_put_oi = st.session_state.daily_baseline.get(strike, {}).get('put_oi', curr_put_oi)
        
        call_chg_list.append(curr_call_oi - base_call_oi)
        put_chg_list.append(curr_put_oi - base_put_oi)
        
    active_strikes_df['call_chg_oi'] = call_chg_list
    active_strikes_df['put_chg_oi'] = put_chg_list

# -------------------------------------------------------------------
# 6. GLOBAL DATA PREPARATION 
# -------------------------------------------------------------------
greeks_display_df = pd.DataFrame()
resistance_strike = 0
support_strike = 0
battleground_strike = 0
current_vix = 15.0

if chain_df is not None and not active_strikes_df.empty:
    # --- Greeks Data Prep ---
    required_cols = {
        'call_options.option_greeks.iv': 'Call IV',
        'call_options.option_greeks.delta': 'Call Delta',
        'call_options.option_greeks.gamma': 'Call Gamma',
        'call_options.option_greeks.theta': 'Call Theta',
        'call_options.option_greeks.vega': 'Call Vega',
        'strike_price': 'STRIKE',
        'put_options.option_greeks.iv': 'Put IV',
        'put_options.option_greeks.delta': 'Put Delta',
        'put_options.option_greeks.gamma': 'Put Gamma',
        'put_options.option_greeks.theta': 'Put Theta',
        'put_options.option_greeks.vega': 'Put Vega',
    }
    available_cols = [c for c in required_cols.keys() if c in active_strikes_df.columns]
    greeks_display_df = active_strikes_df[available_cols].rename(columns=required_cols).round(4)

    # --- S&R and Battleground Prep ---
    try:
        res_idx = active_strikes_df['call_options.market_data.oi'].idxmax()
        resistance_strike = active_strikes_df.loc[res_idx, 'strike_price']
        sup_idx = active_strikes_df['put_options.market_data.oi'].idxmax()
        support_strike = active_strikes_df.loc[sup_idx, 'strike_price']
    except Exception:
        resistance_strike, support_strike = 0, 0

    active_strikes_df['Total_Activity'] = active_strikes_df.get('call_options.market_data.oi', 0) + active_strikes_df.get('put_options.market_data.oi', 0)
    try:
        bg_idx = active_strikes_df['Total_Activity'].idxmax()
        battleground_strike = active_strikes_df.loc[bg_idx, 'strike_price'] if pd.notna(bg_idx) else 0
    except Exception:
        battleground_strike = 0

    # --- Time-Series & VIX Prep ---
    try:
        current_vix = float(live_vix)
    except Exception:
        current_vix = 15.0

    if 'history_df' not in st.session_state:
        st.session_state.history_df = pd.DataFrame(columns=['Time_IST', 'PCR', 'VIX'])
    ist = pytz.timezone('Asia/Kolkata')
    current_time_str = datetime.now(ist).strftime('%H:%M:%S')
    
    new_data = pd.DataFrame([{'Time_IST': current_time_str, 'PCR': live_pcr, 'VIX': current_vix}])
    st.session_state.history_df = pd.concat([st.session_state.history_df, new_data], ignore_index=True)
    st.session_state.history_df = st.session_state.history_df.tail(20)

# ===================================================================
# UI RENDERING
# ===================================================================

# -------------------------------------------------------------------
# 7. LIVE OPTION CHAIN DYNAMIC TABLE (ATM ± 5 Strikes)
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.markdown("---")
    
    st.markdown("#### 🔗 Cumulative Position Tracker (Full Day Shift)")

    oc_required_cols = {
        'put_options.market_data.ltp': 'Put LTP',
        'put_chg_oi': 'Bull Activity',
        'put_options.market_data.oi': 'Bull Positions',
        'strike_price': 'STRIKE',
        'call_options.market_data.oi': 'Bear Positions',
        'call_chg_oi': 'Bear Activity',
        'call_options.market_data.ltp': 'Call LTP'        
    }

    oc_available = [c for c in oc_required_cols.keys() if c in active_strikes_df.columns]
    oc_display_df = active_strikes_df[oc_available].rename(columns=oc_required_cols)

    ordered_cols = ['Bull Positions', 'Bull Activity', 'Call LTP', 'STRIKE', 'Put LTP', 'Bear Activity', 'Bear Positions']
    final_cols = [c for c in ordered_cols if c in oc_display_df.columns]
    oc_display_df = oc_display_df[final_cols]

    for c in final_cols:
        oc_display_df[c] = pd.to_numeric(oc_display_df[c], errors='coerce').fillna(0)

    def style_oc_table(row):
        styles = []
        is_atm = (row['STRIKE'] == atm_strike)
        base_style = 'background-color: rgba(66, 153, 225, 0.3);' if is_atm else ''
        
        # Extract positions for comparison
        try:
            bull_pos = float(row['Bull Positions'])
            bear_pos = float(row['Bear Positions'])
        except Exception:
            bull_pos, bear_pos = 0, 0

        for col in row.index:
            val = row[col]
            if col in ['Bear Activity', 'Bull Activity']:
                if val > 0:
                    styles.append('background-color: #1dc973; color: white;')
                elif val < 0:
                    styles.append('background-color: #ff4b4b; color: white;')
                else:
                    styles.append(base_style)
                    
            # NEW LOGIC: Compare Positions and apply background color
            elif col == 'Bull Positions':
                if bull_pos > bear_pos:
                    styles.append('background-color: rgba(29, 201, 115, 0.35); color: white;')
                else:
                    styles.append(base_style)
                    
            elif col == 'Bear Positions':
                if bear_pos > bull_pos:
                    styles.append('background-color: rgba(255, 75, 75, 0.35); color: white;')
                else:
                    styles.append(base_style)
            else:
                styles.append(base_style)
        return styles

    def format_chg(val):
        if val > 0: return f"+{int(val)}"
        elif val < 0: return f"{int(val)}"
        else: return "0"

    format_dict = {}
    for c in final_cols:
        if 'Chg OI' in c:
            format_dict[c] = format_chg
        elif 'LTP' in c:
            format_dict[c] = '{:.2f}'
        else:
            format_dict[c] = '{:.0f}'

    styled_oc = oc_display_df.style.apply(style_oc_table, axis=1).format(format_dict)
    
    # Force Header Center Alignment via Pandas Styler CSS
    styled_oc = styled_oc.set_table_styles([
        dict(selector='th', props=[('text-align', 'center !important')])
    ], overwrite=False)

    # Streamlit Column Configuration for hard Center Alignment of data cells
    center_alignment_oc = {col: st.column_config.Column(alignment="center") for col in oc_display_df.columns}

    st.dataframe(
        styled_oc, 
        use_container_width=True, 
        hide_index=True, 
        height=430,
        column_config=center_alignment_oc
    )

    # --- SUMMARY ROWS (MACRO ATM±5 & MICRO ATM±2) ---
    
    # 1. Macro Data (Full ATM ± 5 Chain)
    bull_pos_macro = oc_display_df['Bull Positions'].mean() if not oc_display_df.empty else 0
    bull_act_macro = oc_display_df['Bull Activity'].mean() if not oc_display_df.empty else 0
    bear_pos_macro = oc_display_df['Bear Positions'].mean() if not oc_display_df.empty else 0
    bear_act_macro = oc_display_df['Bear Activity'].mean() if not oc_display_df.empty else 0

    # 2. Micro Data (ATM ± 2 Chain)
    atm_idx_list = oc_display_df.index[oc_display_df['STRIKE'] == atm_strike].tolist()
    if atm_idx_list:
        pos = oc_display_df.index.get_loc(atm_idx_list[0])
        micro_oc_df = oc_display_df.iloc[max(0, pos - 2): min(len(oc_display_df), pos + 3)]
    else:
        micro_oc_df = oc_display_df

    bull_pos_micro = micro_oc_df['Bull Positions'].mean() if not micro_oc_df.empty else 0
    bull_act_micro = micro_oc_df['Bull Activity'].mean() if not micro_oc_df.empty else 0
    bear_pos_micro = micro_oc_df['Bear Positions'].mean() if not micro_oc_df.empty else 0
    bear_act_micro = micro_oc_df['Bear Activity'].mean() if not micro_oc_df.empty else 0

    # 3. Unique session state keys for all 8 metrics
    keys = {
        'bp_mac': f"prev_bp_mac_{target_instrument_key}_{selected_expiry}",
        'ba_mac': f"prev_ba_mac_{target_instrument_key}_{selected_expiry}",
        'brp_mac': f"prev_brp_mac_{target_instrument_key}_{selected_expiry}",
        'bra_mac': f"prev_bra_mac_{target_instrument_key}_{selected_expiry}",
        'bp_mic': f"prev_bp_mic_{target_instrument_key}_{selected_expiry}",
        'ba_mic': f"prev_ba_mic_{target_instrument_key}_{selected_expiry}",
        'brp_mic': f"prev_brp_mic_{target_instrument_key}_{selected_expiry}",
        'bra_mic': f"prev_bra_mic_{target_instrument_key}_{selected_expiry}"
    }

    vals = {
        'bp_mac': bull_pos_macro, 'ba_mac': bull_act_macro, 
        'brp_mac': bear_pos_macro, 'bra_mac': bear_act_macro,
        'bp_mic': bull_pos_micro, 'ba_mic': bull_act_micro, 
        'brp_mic': bear_pos_micro, 'bra_mic': bear_act_micro
    }

    # Initialize states & calculate diffs
    diffs = {}
    for k_short, k_full in keys.items():
        if k_full not in st.session_state:
            st.session_state[k_full] = vals[k_short]
        diffs[k_short] = vals[k_short] - st.session_state[k_full]
        st.session_state[k_full] = vals[k_short] # Update for next cycle

    # Helper function to render the up/down arrows
    def get_diff_html(diff):
        if diff > 0:
            return f"<span style='color:#1dc973; font-size:13px; font-weight:600;'>▲ +{int(diff):,}</span>"
        elif diff < 0:
            return f"<span style='color:#ff4b4b; font-size:13px; font-weight:600;'>▼ {int(diff):,}</span>"
        else:
            return f"<span style='color:#888888; font-size:13px; font-weight:600;'>▬ 0</span>"
    
    st.write("") 
    
    # Helper logic for dominant backgrounds
    def get_bg(bull_val, bear_val, is_bull):
        if is_bull:
            return "rgba(29, 201, 115, 0.25)" if bull_val > bear_val else "#1e1e1e"
        else:
            return "rgba(255, 75, 75, 0.25)" if bear_val > bull_val else "#1e1e1e"

    # Macro Backgrounds
    bg_bp_mac = get_bg(bull_pos_macro, bear_pos_macro, True)
    bg_ba_mac = get_bg(bull_act_macro, bear_act_macro, True)
    bg_brp_mac = get_bg(bull_pos_macro, bear_pos_macro, False)
    bg_bra_mac = get_bg(bull_act_macro, bear_act_macro, False)
    
    # Micro Backgrounds
    bg_bp_mic = get_bg(bull_pos_micro, bear_pos_micro, True)
    bg_ba_mic = get_bg(bull_act_micro, bear_act_micro, True)
    bg_brp_mic = get_bg(bull_pos_micro, bear_pos_micro, False)
    bg_bra_mic = get_bg(bull_act_micro, bear_act_micro, False)

    # --- ROW 1: MACRO (ATM ± 5) ---
    st.markdown("<h6 style='margin-bottom: 5px; color: #888;'>MACRO TREND (ATM ± 5)</h6>", unsafe_allow_html=True)
    mac_col1, mac_col2, mac_col3, mac_col4 = st.columns(4)
    
    with mac_col1:
        st.markdown(f"""
        <div style="background-color:{bg_bp_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BULL POS</p>
            <h4 style="margin:5px 0;">{int(bull_pos_macro):,}</h4>
            <div>{get_diff_html(diffs['bp_mac'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mac_col2:
        st.markdown(f"""
        <div style="background-color:{bg_ba_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BULL ACT</p>
            <h4 style="margin:5px 0;">{int(bull_act_macro):,}</h4>
            <div>{get_diff_html(diffs['ba_mac'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mac_col3:
        st.markdown(f"""
        <div style="background-color:{bg_brp_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BEAR POS</p>
            <h4 style="margin:5px 0;">{int(bear_pos_macro):,}</h4>
            <div>{get_diff_html(diffs['brp_mac'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mac_col4:
        st.markdown(f"""
        <div style="background-color:{bg_bra_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BEAR ACT</p>
            <h4 style="margin:5px 0;">{int(bear_act_macro):,}</h4>
            <div>{get_diff_html(diffs['bra_mac'])}</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")
    
    # --- ROW 2: MICRO (ATM ± 2) ---
    st.markdown("<h6 style='margin-bottom: 5px; color: #888;'>MICRO MOMENTUM (ATM ± 2)</h6>", unsafe_allow_html=True)
    mic_col1, mic_col2, mic_col3, mic_col4 = st.columns(4)
    
    with mic_col1:
        st.markdown(f"""
        <div style="background-color:{bg_bp_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BULL POS</p>
            <h4 style="margin:5px 0;">{int(bull_pos_micro):,}</h4>
            <div>{get_diff_html(diffs['bp_mic'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mic_col2:
        st.markdown(f"""
        <div style="background-color:{bg_ba_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BULL ACT</p>
            <h4 style="margin:5px 0;">{int(bull_act_micro):,}</h4>
            <div>{get_diff_html(diffs['ba_mic'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mic_col3:
        st.markdown(f"""
        <div style="background-color:{bg_brp_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BEAR POS</p>
            <h4 style="margin:5px 0;">{int(bear_pos_micro):,}</h4>
            <div>{get_diff_html(diffs['brp_mic'])}</div>
        </div>
        """, unsafe_allow_html=True)
    with mic_col4:
        st.markdown(f"""
        <div style="background-color:{bg_bra_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px; text-align:center;">AVG BEAR ACT</p>
            <h4 style="margin:5px 0;">{int(bear_act_micro):,}</h4>
            <div>{get_diff_html(diffs['bra_mic'])}</div>
        </div>
        """, unsafe_allow_html=True)
# -------------------------------------------------------------------
# 8. POSITION BUILDUP / POSITION SHIFT
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.markdown("---")
    color_call = 'rgba(255, 75, 75, 1)' 
    color_put = 'rgba(29, 201, 115, 1)' 

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.markdown("<h5 style='text-align: center;'>POSITION BUILDUP</h5>", unsafe_allow_html=True)
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df.get('call_options.market_data.oi', pd.Series([0]*len(active_strikes_df))),
            name='CALL', marker_color=color_call
        ))
        fig_oi.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df.get('put_options.market_data.oi', pd.Series([0]*len(active_strikes_df))),
            name='PUT', marker_color=color_put
        ))
        fig_oi.update_layout(
            barmode='group', margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(type='category', tickangle=-45) 
        )
        st.plotly_chart(fig_oi, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_buildup")

    with chart_col2:
        st.markdown("<h5 style='text-align: center;'>POSITION SHIFT (CUMULATIVE DAILY)</h5>", unsafe_allow_html=True)
        fig_chg = go.Figure()
        fig_chg.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df['call_chg_oi'], 
            name='CALL', marker_color=color_call
        ))
        fig_chg.add_trace(go.Bar(
            x=active_strikes_df['strike_price'], 
            y=active_strikes_df['put_chg_oi'], 
            name='PUT', marker_color=color_put
        ))
        fig_chg.update_layout(
            barmode='group', margin=dict(l=0, r=0, t=30, b=0),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(type='category', tickangle=-45)
        )
        st.plotly_chart(fig_chg, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_change")

# -------------------------------------------------------------------
# 9. SUPPORT / RESISTANCE / BATTLEGROUND
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.write("") 
    box_col1, box_col2, box_col3 = st.columns(3)
    
    with box_col1:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">ACTIVE RES (CHG)</p>
            <h3 style="margin:0;">{int(resistance_strike) if resistance_strike else 0}</h3>
        </div>
        """, unsafe_allow_html=True)
        
    with box_col2:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">ACTIVE SUP (CHG)</p>
            <h3 style="margin:0;">{int(support_strike) if support_strike else 0}</h3>
        </div>
        """, unsafe_allow_html=True)
        
    with box_col3:
        st.markdown(f"""
        <div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;">
            <p style="color:#faca2b; margin:0; font-weight:bold; font-size:12px;">BATTLEGROUND</p>
            <h3 style="margin:0;">{int(battleground_strike)}</h3>
        </div>
        """, unsafe_allow_html=True)

# -------------------------------------------------------------------
# 10. MARKET TREND / FEAR INDEX
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.markdown("---")
    row6_col1, row6_col2 = st.columns(2)

    with row6_col1:
        st.markdown("<h5 style='text-align: center;'>MARKET TREND (PCR)</h5>", unsafe_allow_html=True)
        fig_pcr = go.Figure()
        fig_pcr.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['PCR'],
            mode='lines+markers', line=dict(color='#a855f7', width=3) 
        ))
        fig_pcr.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333')
        )
        st.plotly_chart(fig_pcr, use_container_width=True, config={'displayModeBar': False}, key="chart_pcr_trend")

    with row6_col2:
        st.markdown("<h5 style='text-align: center;'>FEAR INDEX (VIX)</h5>", unsafe_allow_html=True)
        fig_vix = go.Figure()
        fig_vix.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['VIX'],
            mode='lines+markers', line=dict(color='#1dc973', width=3) 
        ))
        fig_vix.update_layout(
            margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333')
        )
        st.plotly_chart(fig_vix, use_container_width=True, config={'displayModeBar': False}, key="chart_vix_trend")


# -------------------------------------------------------------------
# 11. POSITIONAL TRADE ENGINE (Macro Trend Follower)
# -------------------------------------------------------------------
if chain_df is not None and live_pcr is not None and live_pcr != 99.9:
    st.markdown("---")
    st.markdown("#### 🎯 Positional Trend Engine")

    # Strict State Machine: NONE -> BUY_CALL / BUY_PUT -> NONE
    if 'positional_state' not in st.session_state: st.session_state.positional_state = "NONE"
    if 'positional_target' not in st.session_state: st.session_state.positional_target = {}

    # --- Simulated Technical Synchronization ---
    # NOTE: You will need to replace these boolean flags with your actual live EMA/DEMA data feeds
    # These prevent the algorithm from entering "Sideways" false breakouts.
    price_above_ema21 = True  
    price_above_dema100 = True 
    
    is_technical_bullish = price_above_ema21 and price_above_dema100
    is_technical_bearish = not price_above_ema21 and not price_above_dema100
    
    # 1. Cumulative Data Summation (Full Chain)
    total_daily_bull_shift = active_strikes_df['put_chg_oi'].sum()
    total_daily_bear_shift = active_strikes_df['call_chg_oi'].sum()

    signal_action = "WAITING FOR MACRO CONFIRMATION 🟡"
    suggested_strike = st.session_state.positional_target.get('strike', 'N/A')

    # --- STATE: FLAT (Hunting for Entry) ---
    if st.session_state.positional_state == "NONE":
        
        # LONG (CE) SETUP: Delta ~0.50
        if (total_daily_bull_shift > total_daily_bear_shift) and is_technical_bullish and (live_pcr > 1.0):
            st.session_state.positional_state = "BUY_CALL"
            closest_idx = (greeks_display_df['Call Delta'] - 0.50).abs().idxmin()
            
            st.session_state.positional_target = {
                'strike': f"{int(greeks_display_df.loc[closest_idx, 'STRIKE'])} CE",
                'entry_spot': underlying_spot
            }
            suggested_strike = st.session_state.positional_target['strike']
            signal_action = f"🚀 NEW LONG TREND DETECTED: BUY {suggested_strike}"
            send_telegram_alert(f"🎯 *POSITIONAL ALERT*\n\n🟢 *Action:* BUY {suggested_strike}\n📊 *Spot:* {underlying_spot}")
            
        # SHORT (PE) SETUP: Delta ~ -0.50
        elif (total_daily_bear_shift > total_daily_bull_shift) and is_technical_bearish and (live_pcr < 0.9):
            st.session_state.positional_state = "BUY_PUT"
            closest_idx = (greeks_display_df['Put Delta'] - (-0.50)).abs().idxmin()
            
            st.session_state.positional_target = {
                'strike': f"{int(greeks_display_df.loc[closest_idx, 'STRIKE'])} PE",
                'entry_spot': underlying_spot
            }
            suggested_strike = st.session_state.positional_target['strike']
            signal_action = f"💥 NEW SHORT TREND DETECTED: BUY {suggested_strike}"
            send_telegram_alert(f"🎯 *POSITIONAL ALERT*\n\n🔴 *Action:* BUY {suggested_strike}\n📊 *Spot:* {underlying_spot}")

    # --- STATE: ACTIVE TRADE (Managing the Position) ---
    elif st.session_state.positional_state == "BUY_CALL":
        # Exit Logic: Trend breaks or PCR collapses
        if is_technical_bearish or (live_pcr < 0.85):
            signal_action = f"🛑 EXIT BUY (CALL): Trend Weakening"
            send_telegram_alert(f"🛑 *EXIT POSITIONAL TRADE*\n\nClose {suggested_strike}. Trend has weakened.")
            st.session_state.positional_state = "NONE" # Reset for next cycle
            st.session_state.positional_target = {} # <--- ADD THIS LINE
        else:
            signal_action = f"🟢 HOLDING {suggested_strike} (Riding Trend)"

    elif st.session_state.positional_state == "BUY_PUT":
        # Exit Logic: Trend breaks or PCR spikes
        if is_technical_bullish or (live_pcr > 1.15):
            signal_action = f"🛑 EXIT BUY (PUT): Trend Weakening"
            send_telegram_alert(f"🛑 *EXIT POSITIONAL TRADE*\n\nClose {suggested_strike}. Trend has weakened.")
            st.session_state.positional_state = "NONE" # Reset for next cycle
            st.session_state.positional_target = {} # <--- ADD THIS LINE
        else:
            signal_action = f"🔴 HOLDING {suggested_strike} (Riding Trend)"

    # Render UI
    col_a, col_b = st.columns(2)
    with col_a:
        st.info(f"**Action Engine:**\n### {signal_action}")
    with col_b:
        st.success(f"**Target Asset:**\n### {suggested_strike}")

    if st.session_state.positional_state != "NONE":
        if st.button("Manual Exit / Reset State"):
            st.session_state.positional_state = "NONE"
            st.session_state.positional_target = {}
            st.rerun()

# -------------------------------------------------------------------
# 12. INTERACTIVE OPTION GREEKS TABLE
# -------------------------------------------------------------------
if not active_strikes_df.empty:
    st.markdown("---")
    ist_tz_greeks = pytz.timezone('Asia/Kolkata')
    current_time_ist_str = datetime.now(ist_tz_greeks).strftime('%Y-%m-%d %H:%M:%S IST')
    
    st.markdown(f"#### Option Greek | ⏱️ {current_time_ist_str}")
    
    # 1. Grab Base Greeks and Merge LTPs from active_strikes_df
    greeks_cols = ['Call Delta', 'Call Gamma', 'Call Theta', 'STRIKE', 'Put Delta', 'Put Gamma', 'Put Theta']
    base_greeks_df = greeks_display_df[greeks_cols].copy()
    
    ltp_df = active_strikes_df[['strike_price', 'call_options.market_data.ltp', 'put_options.market_data.ltp']].copy()
    ltp_df.rename(columns={
        'strike_price': 'STRIKE',
        'call_options.market_data.ltp': 'Call LTP',
        'put_options.market_data.ltp': 'Put LTP'
    }, inplace=True)
    
    # Merge and perfectly order the columns
    base_greeks_df = pd.merge(base_greeks_df, ltp_df, on='STRIKE', how='left')
    display_cols = [
        'Call Delta', 'Call Gamma', 'Call Theta', 'Call LTP', 
        'STRIKE', 
        'Put LTP', 'Put Delta', 'Put Gamma', 'Put Theta'
    ]
    base_greeks_df = base_greeks_df[display_cols]
    
    # 2. State management to track previous values
    state_key_greeks = f"prev_greeks_{target_instrument_key}_{selected_expiry}"
    if state_key_greeks not in st.session_state:
        st.session_state[state_key_greeks] = {}
        
    prev_greeks = st.session_state[state_key_greeks]
    new_greeks_state = {}
    
    # 3. Create a formatted dataframe to hold the strings with arrows
    visual_greeks_df = base_greeks_df.astype(object)
    visual_greeks_df['STRIKE'] = visual_greeks_df['STRIKE'].astype(int) 
    
    metrics = [c for c in display_cols if c != 'STRIKE']
    
    for idx, row in base_greeks_df.iterrows():
        strike = int(row['STRIKE'])
        new_greeks_state[strike] = {}
        
        for col in metrics:
            curr_val = float(row[col])
            new_greeks_state[strike][col] = curr_val
            
            # Dynamically format: 2 decimals for Price (LTP), 4 decimals for Greeks
            fmt = "{:.2f}" if "LTP" in col else "{:.4f}"
            
            # Compare with previous reading and inject arrows
            if strike in prev_greeks and col in prev_greeks[strike]:
                prev_val = prev_greeks[strike][col]
                if curr_val > prev_val:
                    visual_greeks_df.at[idx, col] = f"▲ {fmt.format(curr_val)}"
                elif curr_val < prev_val:
                    visual_greeks_df.at[idx, col] = f"▼ {fmt.format(curr_val)}"
                else:
                    visual_greeks_df.at[idx, col] = fmt.format(curr_val)
            else:
                visual_greeks_df.at[idx, col] = fmt.format(curr_val)
                
    st.session_state[state_key_greeks] = new_greeks_state
    
    # 4. Apply dynamic CSS colors based on the arrows
    def style_greeks(row):
        styles = []
        is_atm = row['STRIKE'] == int(atm_strike)
        base_style = 'background-color: rgba(255, 255, 0, 0.2); ' if is_atm else ''
        
        for col in row.index:
            if col == 'STRIKE':
                styles.append(base_style + 'font-weight: bold;')
                continue
                
            val = str(row[col])
            if val.startswith('▲'):
                styles.append(base_style + 'color: #1dc973; font-weight: 600;')
            elif val.startswith('▼'):
                styles.append(base_style + 'color: #ff4b4b; font-weight: 600;')
            else:
                styles.append(base_style)
        return styles

    styled_greeks = visual_greeks_df.style.apply(style_greeks, axis=1)
    
    # Streamlit Column Configuration for hard Center Alignment
    center_alignment = {col: st.column_config.Column(alignment="center") for col in visual_greeks_df.columns}

    st.dataframe(
        styled_greeks, 
        use_container_width=True, 
        hide_index=True, 
        height=430,
        column_config=center_alignment
    )
    
elif ACCESS_TOKEN == "YOUR_UPSTOX_ACCESS_TOKEN_HERE":
    st.warning("Please hardcode your valid Upstox Access Token at the top of the script code.")
else:
    st.info("Awaiting valid selection to populate data.")

if st.button("Logout 🚪", use_container_width=True):
        logout()
