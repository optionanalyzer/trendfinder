import streamlit as st
import pandas as pd
import numpy as np
import requests
import urllib.parse
from datetime import datetime, time as dtime
import pytz
import json
import os
import gzip
import sqlite3
import uuid
import hashlib
import time
from dataclasses import dataclass
from typing import List
from streamlit_autorefresh import st_autorefresh
import plotly.graph_objects as go

# ===================================================================
# 0. PAGE CONFIGURATION & GLOBAL VARIABLES
# ===================================================================
st.set_page_config(page_title="Market Terminal", layout="wide", page_icon="📈")

IST = pytz.timezone("Asia/Kolkata")
BASE_URL = "https://api.upstox.com/v2"

# ⚠️ HARDCODE YOUR ACCESS TOKEN HERE 
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJrZXlfaWQiOiJza192MS4wIiwiYWxnIjoiSFMyNTYifQ.eyJzdWIiOiIzTUJDMzIiLCJqdGkiOiI2YTkwNDljOGZiZmVkMzMwYmI0ZGEwY2IiLCJpc011bHRpQ2xpZW50IjpmYWxzZSwiaXNQbHVzUGxhbiI6ZmFsc2UsImlzRXh0ZW5kZWQiOnRydWUsImlhdCI6MTc4Nzg0MDk2OCwiaXNzIjoidWRhcGktZ2F0ZXdheS1zZXJ2aWNlIiwiZXhwIjoxODE5NDA0MDAwfQ.q4pIS3RWrJeWDl3KvPosg0kO0vKRSIAKXXZ2Sx4j3jU" 

TELEGRAM_BOT_TOKEN = "8968266056:AAFlTouDWGZQInTpp3SFEZINw3Nj8YL5cxI"
TELEGRAM_CHAT_ID = ""

# ===================================================================
# 1. DATABASE & SINGLE-SESSION AUTHENTICATION ENGINE
# ===================================================================
def init_db():
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, active_session TEXT)''')
    
    default_pw = hashlib.sha256('admin123'.encode()).hexdigest()
    c.execute("INSERT OR IGNORE INTO users (username, password, active_session) VALUES (?, ?, ?)", 
              ('admin', default_pw, None))
    conn.commit()
    conn.close()

init_db()

if 'session_id' not in st.session_state:
    st.session_state.session_id = None
if 'username' not in st.session_state:
    st.session_state.username = None

def verify_session():
    if not st.session_state.session_id or not st.session_state.username:
        return False
    conn = sqlite3.connect('fno_users.db')
    c = conn.cursor()
    c.execute("SELECT active_session FROM users WHERE username=?", (st.session_state.username,))
    row = c.fetchone()
    conn.close()
    if row and row[0] == st.session_state.session_id:
        return True
    return False

def logout():
    if st.session_state.username:
        conn = sqlite3.connect('fno_users.db')
        c = conn.cursor()
        c.execute("UPDATE users SET active_session = NULL WHERE username=?", (st.session_state.username,))
        conn.commit()
        conn.close()
    st.session_state.session_id = None
    st.session_state.username = None
    st.rerun()

# --- LOGIN SCREEN ---
if not verify_session():
    st.markdown("<h2 style='text-align: center;'>🔐 Market Intelligence Terminal</h2>", unsafe_allow_html=True)
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
    st.stop()

# ===================================================================
# 2. GLOBAL APP REFRESH, DATA LOADING & TELEGRAM SETUP
# ===================================================================
# Run a seamless background refresh every 1-min for the entire app
st_autorefresh(interval=100000, limit=None, key="market_terminal_refresh")

def send_telegram_alert(message):
    if TELEGRAM_BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE" or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=2) 
    except Exception:
        pass

@st.cache_resource
def notify_server_start():
    send_telegram_alert("🚀 *Market Terminal Started Successfully*")
    return True

notify_server_start()

# Load Global Dataframes to fix NameError
@st.cache_data(show_spinner="Fetching Master Instrument List from Upstox...", ttl=86400)
def load_instruments_global():
    url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
    df = pd.read_csv(url)
    fno_df = df[
        df['instrument_key'].str.startswith('NSE_FO|') | 
        df['instrument_key'].str.startswith('NSE_INDEX|') |
        df['instrument_key'].str.startswith('BSE_FO|') |
        df['instrument_key'].str.startswith('BSE_INDEX|')
    ]
    return df, fno_df

master_df, fno_df = load_instruments_global()

# Determine FNO Stocks and ensure major indices are on top
raw_fno_symbols = [s for s in master_df['name'].dropna().unique().tolist() if isinstance(s, str)]
FNO_STOCKS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"] + sorted([s for s in raw_fno_symbols if s not in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]])

# Logout Button at Top Right
col_title, col_logout = st.columns([9, 1])
with col_title:
    st.markdown("## 📊 Combined Market Terminal")
with col_logout:
    if st.button("Logout 🚪"):
        logout()
st.markdown("---")

# ===================================================================
# 3. TABS INITIALIZATION (MCX Tab Removed)
# ===================================================================
tab1, tab2, tab3 = st.tabs(["🎯 FnO Positional Terminal", "📈 Momentum Scanner", "🏢 Sector Scope"])

# ===================================================================
# TAB 1: FNO POSITIONAL TERMINAL (Max Pain + OI Line Graph Added)
# ===================================================================
with tab1:
    st.markdown("### 🎯 FnO Positional Intelligence Terminal")
    st.markdown("---")

    def get_expiries_for_symbol(symbol, df):
        symbol_data = df[df['name'] == symbol]
        expiries = symbol_data['expiry'].dropna().unique().tolist()
        return sorted(expiries)

    def get_underlying_ltp(instrument_key, access_token):
        safe_key = urllib.parse.quote(instrument_key)
        url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json().get('data', {})
                if data: return list(data.values())[0].get('last_price')
        except Exception: pass
        return None

    def fetch_live_vix(access_token):
        if not access_token: return "N/A"
        safe_key = urllib.parse.quote("NSE_INDEX|India VIX")
        url = f"https://api.upstox.com/v3/market-quote/ltp?instrument_key={safe_key}"
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
        try:
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json().get('data', {})
                if data:
                    ltp = list(data.values())[0].get('last_price')
                    if ltp is not None: return str(ltp)
            return f"Err: {response.status_code}"
        except Exception: return "N/A"

    def get_option_chain_data(instrument_key, expiry_date, access_token, spot_price):
        if not access_token or not spot_price: return None, None
        safe_key = urllib.parse.quote(instrument_key)
        url = f"https://api.upstox.com/v2/option/chain?instrument_key={safe_key}&expiry_date={expiry_date}"
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
        try:
            response = requests.get(url, headers=headers, timeout=6)
            if response.status_code != 200: return None, None
            data = response.json().get('data', [])
            if not data: return None, None
            chain_df = pd.json_normalize(data)
            chain_df.fillna(0, inplace=True)
            chain_df['distance_from_spot'] = abs(chain_df['strike_price'] - spot_price)
            atm_index = chain_df['distance_from_spot'].idxmin()
            atm_strike = chain_df.loc[atm_index, 'strike_price']
            return chain_df, atm_strike
        except Exception: return None, None

    col1, col2, col3, col4, col5 = st.columns([1.5, 1.5, 1, 1, 1])

    with col1:
        selected_symbol = st.selectbox("Select Instrument", options=FNO_STOCKS, index=None, placeholder="Search for an instrument...", label_visibility="collapsed")
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

    live_vix = fetch_live_vix(ACCESS_TOKEN)
    underlying_spot = get_underlying_ltp(target_instrument_key, ACCESS_TOKEN) if target_instrument_key else None
    chain_df, atm_strike = get_option_chain_data(target_instrument_key, selected_expiry, ACCESS_TOKEN, underlying_spot) if available_expiries and target_instrument_key else (None, None)

    live_pcr = None
    micro_pcr = None
    active_strikes_df = pd.DataFrame()

    if chain_df is not None:
        # 1. Establish a persistent Anchor ATM strike for the entire day
        if 'anchor_atm_strike' not in st.session_state or st.session_state.get('anchor_symbol') != target_instrument_key:
            atm_idx = chain_df['distance_from_spot'].idxmin()
            st.session_state.anchor_atm_strike = chain_df.loc[atm_idx, 'strike_price']
            st.session_state.anchor_symbol = target_instrument_key

        # 2. Slice strictly around the persistent Anchor ATM strike
        anchor_idx = (chain_df['strike_price'] - st.session_state.anchor_atm_strike).abs().idxmin()
        
        active_strikes_df = chain_df.iloc[max(0, anchor_idx - 5):min(len(chain_df) - 1, anchor_idx + 5) + 1].copy()
        total_call_oi = active_strikes_df.get('call_options.market_data.oi', pd.Series([0])).sum()
        total_put_oi = active_strikes_df.get('put_options.market_data.oi', pd.Series([0])).sum()
        live_pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 99.9

        micro_strikes_df = chain_df.iloc[max(0, anchor_idx - 2):min(len(chain_df) - 1, anchor_idx + 2) + 1].copy()
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

    if not active_strikes_df.empty:
        state_prefix = f"{target_instrument_key}_{selected_expiry}"
        current_date_str = datetime.now(IST).strftime('%Y-%m-%d')
        safe_prefix = state_prefix.replace("|", "_").replace(" ", "_")
        baseline_file = f"oi_baseline_{safe_prefix}.json"
        
        def load_local_baseline():
            if os.path.exists(baseline_file):
                try:
                    with open(baseline_file, 'r') as f:
                        saved_data = json.load(f)
                        if saved_data.get('date') == current_date_str:
                            return {float(k): v for k, v in saved_data.get('baseline', {}).items()}
                except Exception: pass
            return {}

        def save_local_baseline(baseline_data):
            try:
                payload = {'date': current_date_str, 'baseline': baseline_data}
                with open(baseline_file, 'w') as f:
                    json.dump(payload, f)
            except Exception: pass

        if st.session_state.get('oi_instrument_tracker') != state_prefix:
            st.session_state.daily_baseline = load_local_baseline()
            st.session_state.oi_instrument_tracker = state_prefix
            
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

        call_chg_list, put_chg_list = [], []
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

    greeks_display_df = pd.DataFrame()
    resistance_strike = 0
    support_strike = 0
    max_pain_strike = 0
    current_vix = 15.0

    if chain_df is not None and not active_strikes_df.empty:
        required_cols = {
            'call_options.option_greeks.iv': 'Call IV', 'call_options.option_greeks.delta': 'Call Delta',
            'call_options.option_greeks.gamma': 'Call Gamma', 'call_options.option_greeks.theta': 'Call Theta',
            'call_options.option_greeks.vega': 'Call Vega', 'strike_price': 'STRIKE',
            'put_options.option_greeks.iv': 'Put IV', 'put_options.option_greeks.delta': 'Put Delta',
            'put_options.option_greeks.gamma': 'Put Gamma', 'put_options.option_greeks.theta': 'Put Theta',
            'put_options.option_greeks.vega': 'Put Vega',
        }
        available_cols = [c for c in required_cols.keys() if c in active_strikes_df.columns]
        greeks_display_df = active_strikes_df[available_cols].rename(columns=required_cols).round(4)

        try:
            res_idx = active_strikes_df['call_options.market_data.oi'].idxmax()
            resistance_strike = active_strikes_df.loc[res_idx, 'strike_price']
            sup_idx = active_strikes_df['put_options.market_data.oi'].idxmax()
            support_strike = active_strikes_df.loc[sup_idx, 'strike_price']
        except Exception:
            resistance_strike, support_strike = 0, 0

        # MAX PAIN ALGORITHM IMPLEMENTATION
        try:
            all_chain_strikes = chain_df['strike_price'].dropna().unique()
            c_oi_series = chain_df.get('call_options.market_data.oi', pd.Series([0]*len(chain_df))).fillna(0)
            p_oi_series = chain_df.get('put_options.market_data.oi', pd.Series([0]*len(chain_df))).fillna(0)

            min_loss = float('inf')
            calculated_max_pain = 0

            for test_k in all_chain_strikes:
                call_loss = np.maximum(0, test_k - chain_df['strike_price']) * c_oi_series
                put_loss = np.maximum(0, chain_df['strike_price'] - test_k) * p_oi_series
                total_loss = (call_loss + put_loss).sum()

                if total_loss < min_loss:
                    min_loss = total_loss
                    calculated_max_pain = test_k

            max_pain_strike = calculated_max_pain
        except Exception:
            max_pain_strike = 0

        try: current_vix = float(live_vix)
        except Exception: current_vix = 15.0

        # Calculate Total ATM ± 5 OI Change in Lakhs
        total_call_chg_lakhs = active_strikes_df['call_chg_oi'].sum() / 100000 if 'call_chg_oi' in active_strikes_df.columns else 0.0
        total_put_chg_lakhs = active_strikes_df['put_chg_oi'].sum() / 100000 if 'put_chg_oi' in active_strikes_df.columns else 0.0

        if 'history_df' not in st.session_state or 'Call_Chg_Lakhs' not in st.session_state.history_df.columns:
            st.session_state.history_df = pd.DataFrame(columns=['Time_IST', 'PCR', 'VIX', 'Call_Chg_Lakhs', 'Put_Chg_Lakhs'])
            
        current_time_str = datetime.now(IST).strftime('%H:%M')
        
        # Only append data if the minute has changed (prevents duplicating rows on refreshes)
        if st.session_state.history_df.empty or st.session_state.history_df.iloc[-1]['Time_IST'] != current_time_str:
            new_data = pd.DataFrame([{
                'Time_IST': current_time_str, 
                'PCR': live_pcr, 
                'VIX': current_vix,
                'Call_Chg_Lakhs': total_call_chg_lakhs,
                'Put_Chg_Lakhs': total_put_chg_lakhs
            }])
            
            st.session_state.history_df = pd.concat([st.session_state.history_df, new_data], ignore_index=True)
            
            # Auto-save to local disk for backtesting
            safe_target_name = target_instrument_key.replace("|", "_").replace(" ", "_")
            save_filename = f"OI_Backtest_{safe_target_name}_{current_date_str}.csv"
            st.session_state.history_df.to_csv(save_filename, index=False)

    if not active_strikes_df.empty:
        st.markdown("---")
        st.markdown("#### 🔗 Cumulative Position Tracker (Full Day Shift)")

        oc_required_cols = {
            'put_options.market_data.ltp': 'Put LTP', 'put_chg_oi': 'Bull Activity',
            'put_options.market_data.oi': 'Bull Positions', 'strike_price': 'STRIKE',
            'call_options.market_data.oi': 'Bear Positions', 'call_chg_oi': 'Bear Activity',
            'call_options.market_data.ltp': 'Call LTP'        
        }
        oc_available = [c for c in oc_required_cols.keys() if c in active_strikes_df.columns]
        oc_display_df = active_strikes_df[oc_available].rename(columns=oc_required_cols)

        ordered_cols = ['Bull Positions', 'Bull Activity', 'Call LTP', 'STRIKE', 'Put LTP', 'Bear Activity', 'Bear Positions']
        final_cols = [c for c in ordered_cols if c in oc_display_df.columns]
        oc_display_df = oc_display_df[final_cols]
        for c in final_cols: oc_display_df[c] = pd.to_numeric(oc_display_df[c], errors='coerce').fillna(0)

        def style_oc_table(row):
            styles = []
            is_atm = (row['STRIKE'] == atm_strike)
            base_style = 'background-color: rgba(66, 153, 225, 0.3);' if is_atm else ''
            try:
                bull_pos = float(row['Bull Positions'])
                bear_pos = float(row['Bear Positions'])
            except Exception:
                bull_pos, bear_pos = 0, 0
            for col in row.index:
                val = row[col]
                if col in ['Bear Activity', 'Bull Activity']:
                    if val > 0: styles.append('background-color: #1dc973; color: white;')
                    elif val < 0: styles.append('background-color: #ff4b4b; color: white;')
                    else: styles.append(base_style)
                elif col == 'Bull Positions':
                    if bull_pos > bear_pos: styles.append('background-color: rgba(29, 201, 115, 0.35); color: white;')
                    else: styles.append(base_style)
                elif col == 'Bear Positions':
                    if bear_pos > bull_pos: styles.append('background-color: rgba(255, 75, 75, 0.35); color: white;')
                    else: styles.append(base_style)
                else: styles.append(base_style)
            return styles

        def format_chg(val):
            if val > 0: return f"+{int(val)}"
            elif val < 0: return f"{int(val)}"
            else: return "0"

        format_dict = {}
        for c in final_cols:
            if 'Chg OI' in c: format_dict[c] = format_chg
            elif 'LTP' in c: format_dict[c] = '{:.2f}'
            else: format_dict[c] = '{:.0f}'

        styled_oc = oc_display_df.style.apply(style_oc_table, axis=1).format(format_dict)
        styled_oc = styled_oc.set_table_styles([dict(selector='th', props=[('text-align', 'center !important')])], overwrite=False)
        st.dataframe(styled_oc, use_container_width=True, hide_index=True, height=430)

        bull_pos_macro = oc_display_df['Bull Positions'].mean() if not oc_display_df.empty else 0
        bull_act_macro = oc_display_df['Bull Activity'].mean() if not oc_display_df.empty else 0
        bear_pos_macro = oc_display_df['Bear Positions'].mean() if not oc_display_df.empty else 0
        bear_act_macro = oc_display_df['Bear Activity'].mean() if not oc_display_df.empty else 0

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

        keys = {
            'bp_mac': f"prev_bp_mac_{target_instrument_key}_{selected_expiry}", 'ba_mac': f"prev_ba_mac_{target_instrument_key}_{selected_expiry}",
            'brp_mac': f"prev_brp_mac_{target_instrument_key}_{selected_expiry}", 'bra_mac': f"prev_bra_mac_{target_instrument_key}_{selected_expiry}",
            'bp_mic': f"prev_bp_mic_{target_instrument_key}_{selected_expiry}", 'ba_mic': f"prev_ba_mic_{target_instrument_key}_{selected_expiry}",
            'brp_mic': f"prev_brp_mic_{target_instrument_key}_{selected_expiry}", 'bra_mic': f"prev_bra_mic_{target_instrument_key}_{selected_expiry}"
        }

        vals = {
            'bp_mac': bull_pos_macro, 'ba_mac': bull_act_macro, 'brp_mac': bear_pos_macro, 'bra_mac': bear_act_macro,
            'bp_mic': bull_pos_micro, 'ba_mic': bull_act_micro, 'brp_mic': bear_pos_micro, 'bra_mic': bear_act_micro
        }

        diffs = {}
        for k_short, k_full in keys.items():
            if k_full not in st.session_state: st.session_state[k_full] = vals[k_short]
            diffs[k_short] = vals[k_short] - st.session_state[k_full]
            st.session_state[k_full] = vals[k_short]

        def get_diff_html(diff):
            if diff > 0: return f"<span style='color:#1dc973; font-size:13px; font-weight:600;'>▲ +{int(diff):,}</span>"
            elif diff < 0: return f"<span style='color:#ff4b4b; font-size:13px; font-weight:600;'>▼ {int(diff):,}</span>"
            else: return f"<span style='color:#888888; font-size:13px; font-weight:600;'>▬ 0</span>"
        
        def get_bg(bull_val, bear_val, is_bull):
            if is_bull: return "rgba(29, 201, 115, 0.25)" if bull_val > bear_val else "#1e1e1e"
            else: return "rgba(255, 75, 75, 0.25)" if bear_val > bull_val else "#1e1e1e"

        bg_bp_mac, bg_ba_mac = get_bg(bull_pos_macro, bear_pos_macro, True), get_bg(bull_act_macro, bear_act_macro, True)
        bg_brp_mac, bg_bra_mac = get_bg(bull_pos_macro, bear_pos_macro, False), get_bg(bull_act_macro, bear_act_macro, False)
        bg_bp_mic, bg_ba_mic = get_bg(bull_pos_micro, bear_pos_micro, True), get_bg(bull_act_micro, bear_act_micro, True)
        bg_brp_mic, bg_bra_mic = get_bg(bull_pos_micro, bear_pos_micro, False), get_bg(bull_act_micro, bear_act_micro, False)

        st.markdown("<h6 style='margin-bottom: 5px; color: #888;'>MACRO TREND (ATM ± 5)</h6>", unsafe_allow_html=True)
        mac_col1, mac_col2, mac_col3, mac_col4 = st.columns(4)
        with mac_col1: st.markdown(f"""<div style="background-color:{bg_bp_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">AVG BULL POS</p><h4 style="margin:5px 0;">{int(bull_pos_macro):,}</h4><div>{get_diff_html(diffs['bp_mac'])}</div></div>""", unsafe_allow_html=True)
        with mac_col2: st.markdown(f"""<div style="background-color:{bg_ba_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">AVG BULL ACT</p><h4 style="margin:5px 0;">{int(bull_act_macro):,}</h4><div>{get_diff_html(diffs['ba_mac'])}</div></div>""", unsafe_allow_html=True)
        with mac_col3: st.markdown(f"""<div style="background-color:{bg_brp_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">AVG BEAR POS</p><h4 style="margin:5px 0;">{int(bear_pos_macro):,}</h4><div>{get_diff_html(diffs['brp_mac'])}</div></div>""", unsafe_allow_html=True)
        with mac_col4: st.markdown(f"""<div style="background-color:{bg_bra_mac}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">AVG BEAR ACT</p><h4 style="margin:5px 0;">{int(bear_act_macro):,}</h4><div>{get_diff_html(diffs['bra_mac'])}</div></div>""", unsafe_allow_html=True)

        st.markdown("<h6 style='margin-bottom: 5px; color: #888;'>MICRO MOMENTUM (ATM ± 2)</h6>", unsafe_allow_html=True)
        mic_col1, mic_col2, mic_col3, mic_col4 = st.columns(4)
        with mic_col1: st.markdown(f"""<div style="background-color:{bg_bp_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">AVG BULL POS</p><h4 style="margin:5px 0;">{int(bull_pos_micro):,}</h4><div>{get_diff_html(diffs['bp_mic'])}</div></div>""", unsafe_allow_html=True)
        with mic_col2: st.markdown(f"""<div style="background-color:{bg_ba_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">AVG BULL ACT</p><h4 style="margin:5px 0;">{int(bull_act_micro):,}</h4><div>{get_diff_html(diffs['ba_mic'])}</div></div>""", unsafe_allow_html=True)
        with mic_col3: st.markdown(f"""<div style="background-color:{bg_brp_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">AVG BEAR POS</p><h4 style="margin:5px 0;">{int(bear_pos_micro):,}</h4><div>{get_diff_html(diffs['brp_mic'])}</div></div>""", unsafe_allow_html=True)
        with mic_col4: st.markdown(f"""<div style="background-color:{bg_bra_mic}; padding:12px; border-radius:8px; text-align:center; border: 1px solid #333;"><p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">AVG BEAR ACT</p><h4 style="margin:5px 0;">{int(bear_act_micro):,}</h4><div>{get_diff_html(diffs['bra_mic'])}</div></div>""", unsafe_allow_html=True)

        st.markdown("---")
        color_call, color_put = 'rgba(255, 75, 75, 1)', 'rgba(29, 201, 115, 1)' 
        chart_col1, chart_col2 = st.columns(2)

        with chart_col1:
            st.markdown("<h5 style='text-align: center;'>POSITION BUILDUP</h5>", unsafe_allow_html=True)
            fig_oi = go.Figure()
            fig_oi.add_trace(go.Bar(x=active_strikes_df['strike_price'], y=active_strikes_df.get('call_options.market_data.oi', pd.Series([0]*len(active_strikes_df))), name='CALL', marker_color=color_call))
            fig_oi.add_trace(go.Bar(x=active_strikes_df['strike_price'], y=active_strikes_df.get('put_options.market_data.oi', pd.Series([0]*len(active_strikes_df))), name='PUT', marker_color=color_put))
            fig_oi.update_layout(barmode='group', margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), xaxis=dict(type='category', tickangle=-45))
            st.plotly_chart(fig_oi, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_buildup")

        with chart_col2:
            st.markdown("<h5 style='text-align: center;'>POSITION SHIFT (CUMULATIVE DAILY)</h5>", unsafe_allow_html=True)
            fig_chg = go.Figure()
            fig_chg.add_trace(go.Bar(x=active_strikes_df['strike_price'], y=active_strikes_df['call_chg_oi'], name='CALL', marker_color=color_call))
            fig_chg.add_trace(go.Bar(x=active_strikes_df['strike_price'], y=active_strikes_df['put_chg_oi'], name='PUT', marker_color=color_put))
            fig_chg.update_layout(barmode='group', margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), xaxis=dict(type='category', tickangle=-45))
            st.plotly_chart(fig_chg, use_container_width=True, config={'displayModeBar': False}, key="chart_oi_change")

        # TIME-SERIES LINE CHART FOR CUMULATIVE OI SHIFT IN LAKHS
        st.markdown("<h5 style='text-align: center; margin-top: 15px;'>MARKET CONTROL OVER TIME</h5>", unsafe_allow_html=True)
        fig_line_chg = go.Figure()

        fig_line_chg.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], 
            y=st.session_state.history_df['Call_Chg_Lakhs'], 
            mode='lines+markers', 
            name='Total CALL CHG (Lakhs)', 
            line=dict(color=color_call, width=3)
        ))
        fig_line_chg.add_trace(go.Scatter(
            x=st.session_state.history_df['Time_IST'], 
            y=st.session_state.history_df['Put_Chg_Lakhs'], 
            mode='lines+markers', 
            name='Total PUT CHG (Lakhs)', 
            line=dict(color=color_put, width=3)
        ))
        
        fig_line_chg.update_layout(
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor='rgba(0,0,0,0)', 
            plot_bgcolor='rgba(0,0,0,0)', 
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(
                type='category', 
                tickangle=-45, 
                showgrid=True, 
                gridcolor='#333', 
                title="Time (IST)",
                rangeslider=dict(visible=True, thickness=0.05)
            ),
            yaxis=dict(showgrid=True, gridcolor='#333', title="Total Change (Lakhs)"),
            hovermode="x unified"
        )
        
        # Render the chart with the download button enabled in the top right corner
        st.plotly_chart(
            fig_line_chg, 
            use_container_width=True, 
            config={
                'displayModeBar': True,
                'displaylogo': False,
                'modeBarButtonsToRemove': ['lasso2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'resetScale2d'],
                'toImageButtonOptions': {'format': 'png', 'filename': f"OI_Shift_{target_instrument_key.replace('|', '_')}"}
            }, 
            key="chart_line_chg_fno_time"
        )
        
        # Provide a button to download the raw data for back-testing
        csv_data = st.session_state.history_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Historical Data (CSV)",
            data=csv_data,
            file_name=f"OI_History_{current_date_str}.csv",
            mime="text/csv",
        )

        st.write("") 
        box_col1, box_col2, box_col3 = st.columns(3)
        with box_col1: st.markdown(f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;"><p style="color:#ff4b4b; margin:0; font-weight:bold; font-size:12px;">ACTIVE RES (OI)</p><h3 style="margin:0;">{int(resistance_strike) if resistance_strike else 0}</h3></div>""", unsafe_allow_html=True)
        with box_col2: st.markdown(f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;"><p style="color:#1dc973; margin:0; font-weight:bold; font-size:12px;">ACTIVE SUP (OI)</p><h3 style="margin:0;">{int(support_strike) if support_strike else 0}</h3></div>""", unsafe_allow_html=True)
        with box_col3: st.markdown(f"""<div style="background-color:#1e1e1e; padding:15px; border-radius:10px; text-align:center;"><p style="color:#faca2b; margin:0; font-weight:bold; font-size:12px;">MAX PAIN</p><h3 style="margin:0;">{int(max_pain_strike)}</h3></div>""", unsafe_allow_html=True)

        st.markdown("---")
        row6_col1, row6_col2 = st.columns(2)
        with row6_col1:
            st.markdown("<h5 style='text-align: center;'>MARKET TREND (PCR)</h5>", unsafe_allow_html=True)
            fig_pcr = go.Figure()
            fig_pcr.add_trace(go.Scatter(x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['PCR'], mode='lines+markers', line=dict(color='#a855f7', width=3)))
            fig_pcr.update_layout(margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333'))
            st.plotly_chart(fig_pcr, use_container_width=True, config={'displayModeBar': False}, key="chart_pcr_trend")
        with row6_col2:
            st.markdown("<h5 style='text-align: center;'>FEAR INDEX (VIX)</h5>", unsafe_allow_html=True)
            fig_vix = go.Figure()
            fig_vix.add_trace(go.Scatter(x=st.session_state.history_df['Time_IST'], y=st.session_state.history_df['VIX'], mode='lines+markers', line=dict(color='#1dc973', width=3)))
            fig_vix.update_layout(margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(showgrid=True, gridcolor='#333'), yaxis=dict(showgrid=True, gridcolor='#333'))
            st.plotly_chart(fig_vix, use_container_width=True, config={'displayModeBar': False}, key="chart_vix_trend")

    if chain_df is not None and live_pcr is not None and live_pcr != 99.9:
        st.markdown("---")
        st.markdown("#### 🎯 Positional Trend Engine")

        if 'positional_state' not in st.session_state: st.session_state.positional_state = "NONE"
        if 'positional_target' not in st.session_state: st.session_state.positional_target = {}

        price_above_ema21, price_above_dema100 = True, True 
        is_technical_bullish = price_above_ema21 and price_above_dema100
        is_technical_bearish = not price_above_ema21 and not price_above_dema100
        
        total_daily_bull_shift = active_strikes_df['put_chg_oi'].sum()
        total_daily_bear_shift = active_strikes_df['call_chg_oi'].sum()
        signal_action = "WAITING FOR MACRO CONFIRMATION 🟡"
        suggested_strike = st.session_state.positional_target.get('strike', 'N/A')

        if st.session_state.positional_state == "NONE":
            if (total_daily_bull_shift > total_daily_bear_shift) and is_technical_bullish and (live_pcr > 1.0):
                st.session_state.positional_state = "BUY_CALL"
                closest_idx = (greeks_display_df['Call Delta'] - 0.50).abs().idxmin()
                st.session_state.positional_target = {'strike': f"{int(greeks_display_df.loc[closest_idx, 'STRIKE'])} CE", 'entry_spot': underlying_spot}
                suggested_strike = st.session_state.positional_target['strike']
                signal_action = f"🚀 NEW LONG TREND DETECTED: BUY {suggested_strike}"
                send_telegram_alert(f"🎯 *POSITIONAL ALERT*\n\n🟢 *Action:* BUY {suggested_strike}\n📊 *Spot:* {underlying_spot}")
                
            elif (total_daily_bear_shift > total_daily_bull_shift) and is_technical_bearish and (live_pcr < 0.9):
                st.session_state.positional_state = "BUY_PUT"
                closest_idx = (greeks_display_df['Put Delta'] - (-0.50)).abs().idxmin()
                st.session_state.positional_target = {'strike': f"{int(greeks_display_df.loc[closest_idx, 'STRIKE'])} PE", 'entry_spot': underlying_spot}
                suggested_strike = st.session_state.positional_target['strike']
                signal_action = f"💥 NEW SHORT TREND DETECTED: BUY {suggested_strike}"
                send_telegram_alert(f"🎯 *POSITIONAL ALERT*\n\n🔴 *Action:* BUY {suggested_strike}\n📊 *Spot:* {underlying_spot}")

        elif st.session_state.positional_state == "BUY_CALL":
            if is_technical_bearish or (live_pcr < 0.85):
                signal_action = f"🛑 EXIT BUY (CALL): Trend Weakening"
                send_telegram_alert(f"🛑 *EXIT POSITIONAL TRADE*\n\nClose {suggested_strike}. Trend has weakened.")
                st.session_state.positional_state, st.session_state.positional_target = "NONE", {}
            else: signal_action = f"🟢 HOLDING {suggested_strike} (Riding Trend)"

        elif st.session_state.positional_state == "BUY_PUT":
            if is_technical_bullish or (live_pcr > 1.15):
                signal_action = f"🛑 EXIT BUY (PUT): Trend Weakening"
                send_telegram_alert(f"🛑 *EXIT POSITIONAL TRADE*\n\nClose {suggested_strike}. Trend has weakened.")
                st.session_state.positional_state, st.session_state.positional_target = "NONE", {}
            else: signal_action = f"🔴 HOLDING {suggested_strike} (Riding Trend)"

        col_a, col_b = st.columns(2)
        with col_a: st.info(f"**Action Engine:**\n### {signal_action}")
        with col_b: st.success(f"**Target Asset:**\n### {suggested_strike}")

        if st.session_state.positional_state != "NONE":
            if st.button("Manual Exit / Reset State"):
                st.session_state.positional_state, st.session_state.positional_target = "NONE", {}
                st.rerun()

    if not active_strikes_df.empty:
        st.markdown("---")
        current_time_ist_str = datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')
        st.markdown(f"#### Option Greek | ⏱️ {current_time_ist_str}")
        
        greeks_cols = ['Call Delta', 'Call Gamma', 'Call Theta', 'STRIKE', 'Put Delta', 'Put Gamma', 'Put Theta']
        base_greeks_df = greeks_display_df[greeks_cols].copy()
        
        ltp_df = active_strikes_df[['strike_price', 'call_options.market_data.ltp', 'put_options.market_data.ltp']].copy()
        ltp_df.rename(columns={'strike_price': 'STRIKE', 'call_options.market_data.ltp': 'Call LTP', 'put_options.market_data.ltp': 'Put LTP'}, inplace=True)
        
        base_greeks_df = pd.merge(base_greeks_df, ltp_df, on='STRIKE', how='left')
        display_cols = ['Call Delta', 'Call Gamma', 'Call Theta', 'Call LTP', 'STRIKE', 'Put LTP', 'Put Delta', 'Put Gamma', 'Put Theta']
        base_greeks_df = base_greeks_df[display_cols]
        
        state_key_greeks = f"prev_greeks_{target_instrument_key}_{selected_expiry}"
        if state_key_greeks not in st.session_state: st.session_state[state_key_greeks] = {}
        prev_greeks = st.session_state[state_key_greeks]
        new_greeks_state = {}
        
        visual_greeks_df = base_greeks_df.astype(object)
        visual_greeks_df['STRIKE'] = visual_greeks_df['STRIKE'].astype(int) 
        metrics = [c for c in display_cols if c != 'STRIKE']
        
        for idx, row in base_greeks_df.iterrows():
            strike = int(row['STRIKE'])
            new_greeks_state[strike] = {}
            for col in metrics:
                curr_val = float(row[col])
                new_greeks_state[strike][col] = curr_val
                fmt = "{:.2f}" if "LTP" in col else "{:.4f}"
                if strike in prev_greeks and col in prev_greeks[strike]:
                    prev_val = prev_greeks[strike][col]
                    if curr_val > prev_val: visual_greeks_df.at[idx, col] = f"▲ {fmt.format(curr_val)}"
                    elif curr_val < prev_val: visual_greeks_df.at[idx, col] = f"▼ {fmt.format(curr_val)}"
                    else: visual_greeks_df.at[idx, col] = fmt.format(curr_val)
                else: visual_greeks_df.at[idx, col] = fmt.format(curr_val)
        st.session_state[state_key_greeks] = new_greeks_state
        
        def style_greeks(row):
            styles = []
            is_atm = row['STRIKE'] == int(atm_strike)
            base_style = 'background-color: rgba(255, 255, 0, 0.2); ' if is_atm else ''
            for col in row.index:
                if col == 'STRIKE':
                    styles.append(base_style + 'font-weight: bold;')
                    continue
                val = str(row[col])
                if val.startswith('▲'): styles.append(base_style + 'color: #1dc973; font-weight: 600;')
                elif val.startswith('▼'): styles.append(base_style + 'color: #ff4b4b; font-weight: 600;')
                else: styles.append(base_style)
            return styles

        styled_greeks = visual_greeks_df.style.apply(style_greeks, axis=1)
        center_alignment = {col: st.column_config.Column(alignment="center") for col in visual_greeks_df.columns}
        st.dataframe(styled_greeks, use_container_width=True, hide_index=True, height=430, column_config=center_alignment)
# ===================================================================
# TAB 2: MOMENTUM SCANNER (Strict Technicals + VWAP + RVOL + Persistent Memory)
# ===================================================================
with tab2:
    @dataclass
    class ScannerRow:
        symbol: str
        ltp: float
        chg_pct: float
        vol_today: float
        vwap_dist: float  
        rvol: float       
        added_at: str

    class UpstoxClient:
        def __init__(self, access_token: str):
            self.access_token = access_token
            self.headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            self.instrument_map: dict = {}

        def _get(self, url: str, params: dict = None):
            r = requests.get(url, headers=self.headers, params=params, timeout=20)
            r.raise_for_status()
            return r.json()

        def load_instruments(self):
            url = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = json.loads(gzip.decompress(r.content))
            for item in data:
                sym   = str(item.get('trading_symbol', ''))
                seg   = str(item.get('segment', ''))
                itype = str(item.get('instrument_type', ''))
                key   = str(item.get('instrument_key', ''))
                if sym in FNO_STOCKS and seg == 'NSE_EQ' and itype == 'EQ' and key:
                    self.instrument_map[sym] = key
            if not self.instrument_map:
                raise RuntimeError("No FnO symbols matched in instrument file.")
            return self.instrument_map

        def _candles_to_df(self, candles: list) -> pd.DataFrame:
            if not candles: return pd.DataFrame(columns=['ts','open','high','low','close','volume','oi'])
            col_names = ['ts','open','high','low','close','volume','oi'][:len(candles[0])]
            df = pd.DataFrame(candles, columns=col_names)
            df['ts'] = pd.to_datetime(df['ts'], utc=True)
            for c in ['open','high','low','close','volume']:
                if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
            return df.sort_values('ts').reset_index(drop=True)

        def get_historical(self, instrument_key: str, interval: str, to_date: str, from_date: str) -> pd.DataFrame:
            safe_key = urllib.parse.quote(instrument_key)
            url = f"{BASE_URL}/historical-candle/{safe_key}/{interval}/{to_date}/{from_date}"
            try:
                data = self._get(url)
                return self._candles_to_df(data.get('data', {}).get('candles', []))
            except Exception: return pd.DataFrame()

        def get_intraday(self, instrument_key: str, interval: str = '1minute') -> pd.DataFrame:
            safe_key = urllib.parse.quote(instrument_key)
            url = f"{BASE_URL}/historical-candle/intraday/{safe_key}/{interval}"
            try:
                data = self._get(url)
                return self._candles_to_df(data.get('data', {}).get('candles', []))
            except Exception: return pd.DataFrame()

    def calc_ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    def calc_rsi(series: pd.Series, period: int = 10) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return (100 - (100 / (1 + rs))).fillna(0)

    def market_phase(now_ist: datetime) -> str:
        t = now_ist.time()
        if t < dtime(9, 15):   return 'Pre-Open'
        if t < dtime(9, 20):   return 'Monitoring'
        if t <= dtime(15, 30): return 'Scanning'
        return 'Closed'

    def build_historical_cache(client: UpstoxClient, symbols: List[str]):
        now = datetime.now(IST)
        today_str = now.strftime('%Y-%m-%d')
        from_daily_str = (now - pd.Timedelta(days=20)).strftime('%Y-%m-%d')
        yesterday_str = (now - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        from_1m_str = (now - pd.Timedelta(days=6)).strftime('%Y-%m-%d')

        bar = st.progress(0, text='Fetching Historical Data...')
        for i, sym in enumerate(symbols):
            key = client.instrument_map[sym]
            
            df_1m_hist = client.get_historical(key, '1minute', yesterday_str, from_1m_str)
            if not df_1m_hist.empty: df_1m_hist['ts'] = df_1m_hist['ts'].dt.tz_convert(IST)
            st.session_state.hist_1m[sym] = df_1m_hist
            bar.progress((i + 1) / len(symbols))
        bar.empty()

    def scan_symbol(client: UpstoxClient, sym: str):
        key = client.instrument_map[sym]
        df_live = client.get_intraday(key, '1minute')
        if df_live.empty: return None, None, None, None, None
            
        df_live['ts'] = df_live['ts'].dt.tz_convert(IST)
        today_vol = float(df_live['volume'].sum())

        df_hist = st.session_state.hist_1m.get(sym, pd.DataFrame())
        df_all = pd.concat([df_hist, df_live]).drop_duplicates(subset='ts').sort_values('ts')
        
        if df_all['ts'].dt.tz is None:
            df_all['ts'] = pd.to_datetime(df_all['ts'], utc=True).dt.tz_convert(IST)
        else:
            df_all['ts'] = df_all['ts'].dt.tz_convert(IST)
            
        df_all.set_index('ts', inplace=True)

        if df_all.empty: return None, None, None, None, None

        today = df_all.index[-1].date()
        today_data = df_all[df_all.index.date == today]
        morning_mask = (today_data.index.time >= dtime(9, 15)) & (today_data.index.time < dtime(9, 30))
        morning_df = today_data[morning_mask]
        
        high15 = morning_df['high'].max() if not morning_df.empty else np.inf
        low15 = morning_df['low'].min() if not morning_df.empty else -np.inf

        df_5m = df_all.resample('5min').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        df_5m['ema5'] = calc_ema(df_5m['close'], 5)
        df_5m['ema21'] = calc_ema(df_5m['close'], 21)
        df_5m['rsi10'] = calc_rsi(df_5m['close'], 10)
        
        df_5m['date'] = df_5m.index.date
        df_5m['typical'] = (df_5m['high'] + df_5m['low'] + df_5m['close']) / 3
        df_5m['vwap'] = (df_5m['typical'] * df_5m['volume']).groupby(df_5m['date']).cumsum() / df_5m['volume'].groupby(df_5m['date']).cumsum()

        df_5m['vol_avg_20'] = df_5m['volume'].rolling(window=20).mean().shift(1)
        df_5m['rvol'] = df_5m['volume'] / df_5m['vol_avg_20']

        last_5m = df_5m.iloc[-1]
        prev_5m = df_5m.iloc[-2] if len(df_5m) > 1 else last_5m

        close = float(last_5m['close'])
        prev_close = float(prev_5m['close'])
        vwap = float(last_5m['vwap'])
        rvol = float(last_5m['rvol']) if pd.notna(last_5m['rvol']) else 0.0
        
        chg_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0
        vwap_dist = ((close - vwap) / vwap * 100) if vwap else 0
        ts_str = df_5m.index[-1].strftime('%H:%M')

        # 1. Strict Entry Criteria (Requires Volume Spike)
        is_bullish = (close > high15) and (last_5m['ema5'] > last_5m['ema21']) and (last_5m['rsi10'] > 50) and (close > vwap) and (rvol >= 1.5)
        is_bearish = (close < low15) and (last_5m['ema5'] < last_5m['ema21']) and (last_5m['rsi10'] < 50) and (close < vwap) and (rvol >= 1.5)

        # 2. Immediate Eviction Criteria (If trend breaks, kick it out regardless of volume)
        is_bull_exit = (last_5m['ema5'] < last_5m['ema21']) or (close < vwap) or (close < low15)
        is_bear_exit = (last_5m['ema5'] > last_5m['ema21']) or (close > vwap) or (close > high15)

        return is_bullish, is_bearish, is_bull_exit, is_bear_exit, (close, chg_pct, today_vol, vwap_dist, rvol, ts_str)

    def rows_to_df(rows: List[ScannerRow]) -> pd.DataFrame:
        cols = ['Symbol', 'LTP', 'Chg %', 'VWAP Dist', 'Rel Vol (RVOL)', 'Eq Volume', 'Time Found']
        if not rows: return pd.DataFrame(columns=cols)
            
        data = [{
            'Symbol': f"https://in.tradingview.com/chart/?symbol=NSE:{r.symbol}", 
            'LTP': f"₹{r.ltp:.2f}", 
            'Chg %': f"{'+' if r.chg_pct >= 0 else ''}{r.chg_pct:.2f}%",
            'VWAP Dist': f"{'+' if r.vwap_dist >= 0 else ''}{r.vwap_dist:.2f}%", 
            'Rel Vol (RVOL)': f"{r.rvol:.1f}x",
            'Eq Volume': f"{int(r.vol_today):,}", 
            'Time Found': r.added_at,
            '_raw_rvol': float(r.rvol)  # NEW: Hidden column for RVOL sorting
        } for r in rows]
        
        df = pd.DataFrame(data)
        
        # FIX: Sort inherently by Relative Volume (RVOL) in decreasing order
        df = df.sort_values(by='_raw_rvol', ascending=False).reset_index(drop=True)
        
        return df.drop(columns=['_raw_rvol'])
    
    # FIX: Initialize persistent memory dictionaries (active_bulls / active_bears)
    for key, default in [('logs', []), ('hist_1m', {}), ('instrument_map', {}), 
                         ('active_bulls', {}), ('active_bears', {}), 
                         ('last_scan', None), ('scan_count', 0), ('cache_built', False)]:
        if key not in st.session_state: st.session_state[key] = default

    st.markdown("## 📈 Multi-Timeframe Momentum Scanner")
    st.caption("Strict 5-Min Entry: Breakout · EMA5>21 · RSI>50 · Price>VWAP · RVOL>1.5x | Stocks are persistently tracked once they trigger.")

    client = UpstoxClient(ACCESS_TOKEN)

    if not st.session_state.instrument_map:
        with st.spinner("Loading NSE_EQ instrument master..."):
            try: st.session_state.instrument_map = client.load_instruments()
            except Exception as e:
                st.error(f"Instrument load failed: {e}")
                st.stop()
    client.instrument_map = st.session_state.instrument_map
    
    symbols = [s for s in FNO_STOCKS if s in client.instrument_map]

    if not st.session_state.cache_built:
        build_historical_cache(client, symbols)
        st.session_state.cache_built = True

    def do_scan():
        now_ist = datetime.now(IST)
        
        if now_ist.time() < dtime(9, 30):
            st.session_state.logs = [f"[{now_ist.strftime('%H:%M:%S')}] Waiting for 9:30 AM to establish 15-min range..."]
            return
            
        scan_logs = []
        data_found = False
        
        for i, sym in enumerate(symbols):
            try:
                is_bull, is_bear, is_bull_exit, is_bear_exit, data = scan_symbol(client, sym)
                
                if data:
                    data_found = True
                    close, chg_pct, today_vol, vwap_dist, rvol, ts_str = data
                    
                    if is_bull:
                        added_time = st.session_state.active_bulls[sym].added_at if sym in st.session_state.active_bulls else ts_str
                        st.session_state.active_bulls[sym] = ScannerRow(sym, close, chg_pct, today_vol, vwap_dist, rvol, added_time)
                        st.session_state.active_bears.pop(sym, None)
                        
                    elif is_bear:
                        added_time = st.session_state.active_bears[sym].added_at if sym in st.session_state.active_bears else ts_str
                        st.session_state.active_bears[sym] = ScannerRow(sym, close, chg_pct, today_vol, vwap_dist, rvol, added_time)
                        st.session_state.active_bulls.pop(sym, None)
                        
                    else:
                        # Check if the stock violates the trend. If yes, evict it. If no, just update live metrics.
                        if sym in st.session_state.active_bulls:
                            if is_bull_exit:
                                st.session_state.active_bulls.pop(sym, None)
                                scan_logs.append(f"Evicted {sym} from Bulls (Trend Broken)")
                            else:
                                old_row = st.session_state.active_bulls[sym]
                                st.session_state.active_bulls[sym] = ScannerRow(sym, close, chg_pct, today_vol, vwap_dist, rvol, old_row.added_at)
                                
                        elif sym in st.session_state.active_bears:
                            if is_bear_exit:
                                st.session_state.active_bears.pop(sym, None)
                                scan_logs.append(f"Evicted {sym} from Bears (Trend Broken)")
                            else:
                                old_row = st.session_state.active_bears[sym]
                                st.session_state.active_bears[sym] = ScannerRow(sym, close, chg_pct, today_vol, vwap_dist, rvol, old_row.added_at)
                            
            except Exception as e:
                scan_logs.append(f"{sym}: {e}")
        
        if not data_found:
            scan_logs.append("⚠️ Warning: No valid live data retrieved for any symbols. Check API limits or Token.")
            
        st.session_state.last_scan = now_ist.strftime('%H:%M:%S')
        st.session_state.scan_count += 1
        st.session_state.logs = [f"[{now_ist.strftime('%H:%M:%S')}] Scan #{st.session_state.scan_count} complete"] + scan_logs[:20]

    do_scan()

    now_ist = datetime.now(IST)
    phase = market_phase(now_ist)

    h1, h2, h3, h4 = st.columns(4)
    with h1: st.metric("Market Phase", phase)
    with h2: st.metric("IST Time", now_ist.strftime('%H:%M:%S'))
    with h3: st.metric("Scans Run", st.session_state.scan_count)
    with h4: st.metric("Last Scan", st.session_state.last_scan or "—")

    # Render dataframes pulling from the persistent state memory dictionaries
    bull_df = rows_to_df(list(st.session_state.active_bulls.values()))
    bear_df = rows_to_df(list(st.session_state.active_bears.values()))

    def render_tables(df: pd.DataFrame, title: str, is_bullish: bool):
        st.markdown(f"### {'🟢' if is_bullish else '🔴'} {title}")
        if df.empty:
            st.info(f"No {title.lower()} setups found right now.")
        else:
            st.dataframe(
                df, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "Symbol": st.column_config.LinkColumn(
                        "Symbol",
                        display_text=r"https://in\.tradingview\.com/chart/\?symbol=NSE:(.*)"
                    )
                }
            )

    render_tables(bull_df, "Bullish Momentum Breakouts", True)
    st.markdown("---")
    render_tables(bear_df, "Bearish Momentum Breakdowns", False)

    with st.expander("📋 Scan Log", expanded=False):
        for line in st.session_state.logs[:30]: st.caption(line)
        
    if phase not in ('Scanning', 'Monitoring'):
        st.warning(f"Market is **{phase}**. Scanner will wait for market hours.")

# ===================================================================
# TAB 3: SECTOR SCOPE (Heat Map & Drill-Down)
# ===================================================================
with tab3:
    st.markdown("## 🏢 Sector Scope Heat Map")
    st.caption("Real-time sector performance sorted by highest percentage gain.")
    
    # Pre-defined list of major Nifty Sectoral Indices
    SECTOR_INDICES = {
        "NIFTY 50": "NSE_INDEX|Nifty 50",
        "NIFTY BANK": "NSE_INDEX|Nifty Bank",
        "NIFTY AUTO": "NSE_INDEX|Nifty Auto",
        "NIFTY FMCG": "NSE_INDEX|Nifty FMCG",
        "NIFTY IT": "NSE_INDEX|Nifty IT",
        "NIFTY MEDIA": "NSE_INDEX|Nifty Media",
        "NIFTY METAL": "NSE_INDEX|Nifty Metal",
        "NIFTY PHARMA": "NSE_INDEX|Nifty Pharma",
        "NIFTY PSU BANK": "NSE_INDEX|Nifty PSU Bank",
        "NIFTY PVT BANK": "NSE_INDEX|Nifty Private Bank",
        "NIFTY REALTY": "NSE_INDEX|Nifty Realty",
        "NIFTY FIN SERVICE": "NSE_INDEX|Nifty Fin Service",
        "NIFTY ENERGY": "NSE_INDEX|Nifty Energy",
        "NIFTY INFRA": "NSE_INDEX|Nifty Infra",
        "NIFTY COMMODITIES": "NSE_INDEX|Nifty Commodities",
        "NIFTY CONSUMPTION": "NSE_INDEX|Nifty Consumption",
        "NIFTY PSE": "NSE_INDEX|Nifty PSE",
    }

    # Hardcoded constituents for drill-down 
    SECTOR_CONSTITUENTS = {
        "NIFTY METAL": ["SAIL", "WELCORP", "NATIONALUM", "HINDALCO", "NMDC", "JSWSTEEL", "TATASTEEL", "HINDZINC", "JINDALSTEL", "JSL", "APLAPOLLO", "HINDCOPPER", "LLOYDSME", "ADANIENT", "VEDL"],
        "NIFTY BANK": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "BANKBARODA", "AUBANK", "FEDERALBNK", "IDFCFIRSTB", "PNB", "BANDHANBNK"],
        "NIFTY IT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM", "LTIM", "PERSISTENT", "COFORGE", "MPHASIS", "LTTS"],
        "NIFTY AUTO": ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "TVSMOTOR", "ASHOKLEY", "BOSCHLTD", "MRF", "BALKRISIND", "MOTHERSON"],
        "NIFTY FMCG": ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "TATACONSUM", "GODREJCP", "DABUR", "MARICO", "COLPAL", "MCDOWELL-N", "UBL", "PGHH", "RADICO", "EMAMILTD", "BALRAMCHIN"],
        "NIFTY PHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "LUPIN", "AUROPHARMA", "BIOCON", "TORNTPHARM", "ALKEM", "GLENMARK", "LAURUSLABS", "SYNGENE", "GRANULES", "IPCALAB"],
        "NIFTY REALTY": ["DLF", "MACROTECH", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "PHOENIXLTD", "BRIGADE", "SOBHA", "MAHLIFE", "SUNTECK"],
        "NIFTY ENERGY": ["RELIANCE", "NTPC", "ONGC", "POWERGRID", "COALINDIA", "TATAPOWER", "IOC", "BPCL", "GAIL", "ADANIGREEN"],
        "NIFTY FIN SERVICE": ["HDFCBANK", "ICICIBANK", "SBIN", "BAJFINANCE", "BAJAJFINSV", "KOTAKBANK", "AXISBANK", "CHOLAFIN", "MUTHOOTFIN", "SRTRANSFIN", "HDFCLIFE", "SBILIFE"],
        "NIFTY PSE": ["COALINDIA", "NTPC", "ONGC", "POWERGRID", "PFC", "RECLTD", "GAIL", "HAL", "BEL", "IRFC", "BHEL", "SAIL", "NMDC"],
        "NIFTY MEDIA": ["ZEEL", "SUNTV", "PVRINOX", "NETWORK18", "TV18BRDCST", "SAREGAMA", "HATHWAY", "NAVKARCORP"],
        "NIFTY INFRA": ["LT", "RELIANCE", "BHARTIARTL", "NTPC", "POWERGRID", "ULTRACEMCO", "GRASIM", "ONGC", "ADANIPORTS", "SHREECEM", "AMBUJACEM", "ACC"]
    }

    def fetch_bulk_quotes(access_token, keys_list):
        if not access_token or access_token == "YOUR_UPSTOX_ACCESS_TOKEN_HERE" or not keys_list:
            return {}
            
        url = "https://api.upstox.com/v2/market-quote/quotes"
        params = {'instrument_key': ",".join(keys_list)}
        headers = {'Accept': 'application/json', 'Authorization': f'Bearer {access_token}'}
        
        try:
            response = requests.get(url, headers=headers, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json().get('data')
                return data if data else {}
        except Exception:
            pass
        return {}

    def get_classic_heat_color(chg):
        if chg >= 1.0: return "#008b3e"      # Strong Green (Breakouts)
        elif chg > 0: return "#27ae60"       # Light Green
        elif chg < -1.0: return "#c0392b"    # Strong Red (Breakdowns)
        elif chg < 0: return "#e74c3c"       # Light Red
        else: return "#7f8c8d"               # Flat Grey
        
    def render_heat_grid(data_list):
        cols = st.columns(5)
        for i, data in enumerate(data_list):
            col = cols[i % 5]
            bg_color = get_classic_heat_color(data['chg_pct'])
            chg_str = f"+{data['chg_pct']:.2f}%" if data['chg_pct'] > 0 else f"{data['chg_pct']:.2f}%"
            
            html_card = f"""
            <div style="background-color: {bg_color}; padding: 12px; border-radius: 6px; color: white; margin-bottom: 15px; box-shadow: 1px 1px 4px rgba(0,0,0,0.1);">
                <div style="font-size: 13px; font-weight: 600; margin-bottom: 10px; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
                    {data['name']}
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 14px; font-weight: bold;">
                    <span>{data['ltp']:,.2f}</span>
                    <span>{chg_str}</span>
                </div>
            </div>
            """
            col.markdown(html_card, unsafe_allow_html=True)

    # -------------------------------------------------------------------
    # 1. RENDER MAIN SECTOR HEAT MAP
    # -------------------------------------------------------------------
    quotes_data = fetch_bulk_quotes(ACCESS_TOKEN, list(SECTOR_INDICES.values()))
    sector_data = []
    
    for name, key in SECTOR_INDICES.items():
        alt_key = key.replace("|", ":")
        q = quotes_data.get(key) or quotes_data.get(alt_key)
        
        if q:
            ltp = q.get('last_price', 0)
            net_change = q.get('net_change', 0)
            prev_close = ltp - net_change
            chg_pct = (net_change / prev_close * 100) if prev_close > 0 else 0.0
            sector_data.append({'name': name, 'ltp': ltp, 'chg_pct': chg_pct})

    if not sector_data:
        st.warning("Awaiting valid API token or market data to populate the Sector Scope.")
    else:
        sector_data.sort(key=lambda x: x['chg_pct'], reverse=True)
        render_heat_grid(sector_data)
        
        st.markdown("---")
        
        # -------------------------------------------------------------------
        # 2. RENDER COMPONENT DRILL-DOWN (Stocks Heat Map)
        # -------------------------------------------------------------------
        st.markdown("### 🔍 Sector Constituents Heat Map")
        
        drill_options = ["None"] + list(SECTOR_CONSTITUENTS.keys())
        selected_sector = st.selectbox("Select a sector to view its underlying stocks:", options=drill_options, index=0)
        
        if selected_sector != "None":
            stock_symbols = SECTOR_CONSTITUENTS[selected_sector]
            
            stock_keys = []
            symbol_to_key = {}
            
            for sym in stock_symbols:
                if 'tradingsymbol' in master_df.columns:
                    mask = ((master_df['name'] == sym) | (master_df['tradingsymbol'] == sym)) & (master_df['instrument_key'].str.startswith('NSE_EQ|'))
                else:
                    mask = (master_df['name'] == sym) & (master_df['instrument_key'].str.startswith('NSE_EQ|'))
                    
                row = master_df[mask]
                
                if not row.empty:
                    key = row.iloc[0]['instrument_key']
                    stock_keys.append(key)
                    symbol_to_key[key] = sym
            
            if stock_keys:
                with st.spinner(f"Fetching live data for {selected_sector} components..."):
                    stock_quotes = fetch_bulk_quotes(ACCESS_TOKEN, stock_keys)
                    
                stock_data = []
                for key, q in stock_quotes.items():
                    orig_key = key.replace(":", "|")
                    sym_name = symbol_to_key.get(orig_key, key)
                    
                    ltp = q.get('last_price', 0)
                    net_change = q.get('net_change', 0)
                    prev_close = ltp - net_change
                    chg_pct = (net_change / prev_close * 100) if prev_close > 0 else 0.0
                    
                    stock_data.append({'name': sym_name, 'ltp': ltp, 'chg_pct': chg_pct})
                
                if stock_data:
                    stock_data.sort(key=lambda x: x['chg_pct'], reverse=True)
                    render_heat_grid(stock_data)
                else:
                    st.error("Failed to retrieve live data for the selected components.")
            else:
                st.error("Could not match the stock symbols to Upstox instrument keys.")