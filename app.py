"""
Cin7Connect — Demo Mode (Read Only)
====================================
- Fetches wholesale orders from Cin7
- Shows what would be synced to HubSpot
- No data is written to any system
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import json
from pathlib import Path

# =============================================================================
# PAGE CONFIG
# =============================================================================
st.set_page_config(
    page_title="Cin7Connect",
    page_icon="🔄",
    layout="wide"
)

# =============================================================================
# CONSTANTS
# =============================================================================
RETAIL_SOURCES = ['shopify retail', 'shopify', 'web', 'website', 'online', 'retail']
WHOLESALE_SOURCES = ['backend', 'wholesale', 'b2b', 'manual']
CONFIG_FILE = Path(".cin7connect_config.json")

# Status filter: Only import these statuses
IMPORTABLE_STATUSES = ['approved', 'dispatched', 'voided']

# =============================================================================
# SESSION STATE
# =============================================================================
if 'fetched_orders' not in st.session_state:
    st.session_state.fetched_orders = None
if 'fetch_since' not in st.session_state:
    st.session_state.fetch_since = None
if 'fetch_until' not in st.session_state:
    st.session_state.fetch_until = None

# =============================================================================
# CONFIG FILE HANDLING
# =============================================================================
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            return {}
    return {}

def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config))

def clear_config():
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()

# =============================================================================
# CLASSIFICATION
# =============================================================================
def classify_order(order: dict) -> str:
    """Classify an order as 'Wholesale' or 'Retail'."""
    source = (order.get('source') or '').lower().strip()
    project = (order.get('projectName') or '').lower().strip()
    company = (order.get('company') or '').strip()
    
    for kw in RETAIL_SOURCES:
        if kw in source or kw in project:
            return 'Retail'
    for kw in WHOLESALE_SOURCES:
        if kw in source or kw in project:
            return 'Wholesale'
    if company and company.upper() not in ['N/A', 'NONE', 'GUEST', 'CUSTOMER']:
        return 'Wholesale'
    return 'Retail'

# =============================================================================
# CIN7 API
# =============================================================================
def test_cin7(username: str, api_key: str) -> tuple:
    try:
        r = requests.get(
            "https://api.cin7.com/api/v1/SalesOrders",
            auth=(username, api_key),
            params={"rows": 1},
            timeout=30
        )
        if r.status_code == 200:
            return True, "Connected"
        elif r.status_code == 401:
            return False, "Invalid credentials"
        else:
            return False, f"Error {r.status_code}"
    except Exception as e:
        return False, str(e)

def fetch_orders(username: str, api_key: str, since: datetime, until: datetime) -> list:
    start_str = since.strftime("%Y-%m-%dT00:00:00Z")
    end_str = until.strftime("%Y-%m-%dT23:59:59Z")
    
    all_orders = []
    page = 1
    
    while True:
        r = requests.get(
            "https://api.cin7.com/api/v1/SalesOrders",
            auth=(username, api_key),
            params={
                "where": f"createdDate >= '{start_str}' AND createdDate <= '{end_str}'",
                "page": page,
                "rows": 250
            },
            timeout=60
        )
        if r.status_code != 200:
            break
        orders = r.json()
        if not orders:
            break
        all_orders.extend(orders)
        if len(orders) < 250:
            break
        page += 1
    
    # Add segment classification
    for o in all_orders:
        o['_segment'] = classify_order(o)
    
    return all_orders

# =============================================================================
# HUBSPOT API
# =============================================================================
def test_hubspot(api_key: str) -> tuple:
    try:
        r = requests.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"limit": 1},
            timeout=30
        )
        if r.status_code == 200:
            return True, "Connected"
        elif r.status_code == 401:
            return False, "Invalid API key"
        else:
            return False, f"Error {r.status_code}"
    except Exception as e:
        return False, str(e)

# =============================================================================
# FILTER ORDERS
# =============================================================================
def filter_orders(orders: list, exclude_shopify: bool, exclude_zero: bool) -> tuple:
    """
    Filter orders into: to_import, to_skip, to_review
    Now includes status filter: only Approved and Voided are importable
    """
    to_import = []
    to_skip = []
    to_review = []
    
    for o in orders:
        source = (o.get('source') or '').lower()
        total = o.get('total', 0) or 0
        segment = o.get('_segment', 'Retail')
        status = (o.get('stage') or o.get('status') or '').lower()
        
        # Skip retail
        if segment == 'Retail':
            to_skip.append(o)
            continue
        
        # Skip excluded sources
        if exclude_shopify and 'shopify retail' in source:
            to_skip.append(o)
            continue
        
        # Check status - only import Approved and Voided
        if status not in IMPORTABLE_STATUSES:
            to_skip.append(o)
            continue
        
        # Handle $0 orders
        if total == 0:
            if exclude_zero:
                to_skip.append(o)
            else:
                to_review.append(o)
            continue
        
        to_import.append(o)
    
    return to_import, to_skip, to_review

# =============================================================================
# DISPLAY HELPERS
# =============================================================================
def order_to_summary(order: dict) -> dict:
    """Convert order to display format with raw numbers for proper sorting."""
    total = order.get('total', 0) or 0
    return {
        'Order #': order.get('reference', ''),
        'Source': order.get('source', ''),
        'Segment': order.get('_segment', ''),
        'Total': float(total),  # Raw number for sorting
        'Company': order.get('company') or order.get('billingCompany') or '',
        'Customer': order.get('customerName') or order.get('contactName') or '',
        'Email': order.get('email') or order.get('memberEmail') or '',
        'Date': (order.get('createdDate') or '')[:10],
        'Status': order.get('stage') or order.get('status') or '',
    }

def get_column_config():
    """Column configuration for currency formatting."""
    return {
        'Total': st.column_config.NumberColumn(
            'Total',
            format='$%.2f'
        )
    }

# =============================================================================
# MAIN APP
# =============================================================================
def main():
    st.title("🔄 Cin7Connect")
    st.subheader("🎭 DEMO MODE - Read Only")
    st.info("This demo connects to real APIs but **never writes any data**. Safe to use with production systems.")
    
    # -------------------------------------------------------------------------
    # SIDEBAR
    # -------------------------------------------------------------------------
    with st.sidebar:
        config = load_config()
        
        st.header("🔌 Connections")
        
        # Cin7
        st.subheader("Cin7 Omni")
        cin7_user = st.text_input("Username", value=config.get('cin7_username', ''))
        cin7_key = st.text_input("API Key", type="password", value=config.get('cin7_api_key', ''))
        
        if st.button("Test Cin7"):
            if cin7_user and cin7_key:
                ok, msg = test_cin7(cin7_user, cin7_key)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")
            else:
                st.error("Enter credentials")
        
        cin7_ok, _ = test_cin7(cin7_user, cin7_key) if cin7_user and cin7_key else (False, "")
        st.caption(f"Status: {'✅ Connected' if cin7_ok else '❌ Not connected'}")
        
        st.divider()
        
        # HubSpot
        st.subheader("HubSpot")
        hs_key = st.text_input("Private App Token", type="password", value=config.get('hubspot_api_key', ''))
        
        if st.button("Test HubSpot"):
            if hs_key:
                ok, msg = test_hubspot(hs_key)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")
            else:
                st.error("Enter API key")
        
        hs_ok, _ = test_hubspot(hs_key) if hs_key else (False, "")
        st.caption(f"Status: {'✅ Connected' if hs_ok else '❌ Not connected'}")
        
        st.divider()
        
        # Filters
        st.header("⚙️ Filters")
        exclude_shopify = st.checkbox("Exclude 'Shopify Retail'", value=True)
        exclude_zero = st.checkbox("Exclude $0.00 orders", value=False, help="$0 orders go to 'Needs Review' if unchecked")
        
        st.divider()
        
        # Remember credentials
        remember = st.checkbox("🔑 Remember credentials", value=config.get('remember', False), help="Save credentials locally")
        if remember:
            save_config({
                'cin7_username': cin7_user,
                'cin7_api_key': cin7_key,
                'hubspot_api_key': hs_key,
                'remember': True
            })
            st.caption("✅ Credentials saved locally")
        else:
            if config.get('remember'):
                clear_config()
        
        st.caption("🔒 Demo mode - no data will be written")
    
    # -------------------------------------------------------------------------
    # MAIN CONTENT
    # -------------------------------------------------------------------------
    st.header("📅 Select Date Range")
    col1, col2 = st.columns(2)
    with col1:
        since_date = st.date_input("From", value=datetime.now() - timedelta(days=7))
    with col2:
        until_date = st.date_input("To", value=datetime.now())
    
    since = datetime.combine(since_date, datetime.min.time())
    until = datetime.combine(until_date, datetime.max.time())
    
    # Fetch button
    if st.button("🔄 Fetch Orders (Read Only)", type="primary", use_container_width=True):
        if not cin7_user or not cin7_key:
            st.error("Enter Cin7 credentials in sidebar")
        else:
            with st.spinner("Fetching orders from Cin7..."):
                orders = fetch_orders(cin7_user, cin7_key, since, until)
            st.session_state.fetched_orders = orders
            st.session_state.fetch_since = since_date
            st.session_state.fetch_until = until_date
            st.success(f"Fetched {len(orders)} orders")
    
    # -------------------------------------------------------------------------
    # RESULTS (from session state)
    # -------------------------------------------------------------------------
    orders = st.session_state.fetched_orders
    if orders is None:
        st.caption("👆 Select a date range and click Fetch Orders")
        return
    
    st.caption(f"🟢 {len(orders)} orders loaded from {st.session_state.fetch_since} to {st.session_state.fetch_until}")
    
    # Filter
    to_import, to_skip, to_review = filter_orders(orders, exclude_shopify, exclude_zero)
    
    # Segment splits
    wholesale_import = [o for o in to_import if o.get('_segment') == 'Wholesale']
    retail_import = [o for o in to_import if o.get('_segment') == 'Retail']
    wholesale_skip = [o for o in to_skip if o.get('_segment') == 'Wholesale']
    retail_skip = [o for o in to_skip if o.get('_segment') == 'Retail']
    
    # Revenue calcs
    import_revenue = sum(o.get('total', 0) or 0 for o in to_import)
    wholesale_revenue = sum(o.get('total', 0) or 0 for o in wholesale_import)
    retail_revenue = sum(o.get('total', 0) or 0 for o in retail_import)
    skip_revenue = sum(o.get('total', 0) or 0 for o in to_skip)
    review_revenue = sum(o.get('total', 0) or 0 for o in to_review)
    
    st.divider()
    
    # -------------------------------------------------------------------------
    # METRICS
    # -------------------------------------------------------------------------
    st.header("📊 Results — All Orders")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Orders Fetched", len(orders))
    with col2:
        st.metric("Would Import", len(to_import), delta=f"${import_revenue:,.0f}")
    with col3:
        st.metric("Needs Review", len(to_review), delta=f"$0 orders")
    with col4:
        st.metric("Would Skip", len(to_skip), delta=f"${skip_revenue:,.0f}")
    with col5:
        st.metric("Total Revenue", f"${import_revenue + review_revenue:,.0f}")
    
    # Segment split
    st.divider()
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🏢 Wholesale")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Import", len(wholesale_import))
        with c2:
            st.metric("Revenue", f"${wholesale_revenue:,.0f}")
        with c3:
            st.metric("Review", len([o for o in to_review if o.get('_segment') == 'Wholesale']))
        with c4:
            st.metric("Skip", len(wholesale_skip))
    
    with col2:
        st.subheader("🛍️ Retail")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Import", len(retail_import))
        with c2:
            st.metric("Revenue", f"${retail_revenue:,.0f}")
        with c3:
            st.metric("Review", len([o for o in to_review if o.get('_segment') == 'Retail']))
        with c4:
            st.metric("Skip", len(retail_skip))
    
    st.divider()
    
    # -------------------------------------------------------------------------
    # IMPORT TABLE
    # -------------------------------------------------------------------------
    st.subheader(f"✅ Would Import to HubSpot ({len(to_import)} orders)")
    
    tab1, tab2, tab3 = st.tabs([f"All ({len(to_import)})", f"🏢 Wholesale ({len(wholesale_import)})", f"🛍️ Retail ({len(retail_import)})"])
    
    with tab1:
        if to_import:
            df = pd.DataFrame([order_to_summary(o) for o in to_import])
            st.dataframe(df, use_container_width=True, hide_index=True, column_config=get_column_config())
        else:
            st.info("No orders to import")
    
    with tab2:
        if wholesale_import:
            df = pd.DataFrame([order_to_summary(o) for o in wholesale_import])
            st.dataframe(df, use_container_width=True, hide_index=True, column_config=get_column_config())
        else:
            st.info("No wholesale orders to import")
    
    with tab3:
        if retail_import:
            df = pd.DataFrame([order_to_summary(o) for o in retail_import])
            st.dataframe(df, use_container_width=True, hide_index=True, column_config=get_column_config())
        else:
            st.info("No retail orders to import")
    
    # What would be created
    with st.expander("🔍 What would be created in HubSpot"):
        st.markdown("""
        For each imported order, we would create:
        - **Deal**: Order total as Closed Won deal
        - **Line Items**: Each product in the order
        - **Associations**: Link to existing Contact & Company
        
        **Status Filter**: Only **Approved** and **Voided** orders are imported.
        Draft, Pending, Cancelled, and other statuses are skipped.
        """)
    
    st.divider()
    
    # -------------------------------------------------------------------------
    # REVIEW TABLE
    # -------------------------------------------------------------------------
    if to_review:
        st.subheader(f"⚠️ Needs Review ({len(to_review)} orders)")
        st.warning("These $0.00 orders need manual review before import")
        df_review = pd.DataFrame([order_to_summary(o) for o in to_review])
        st.dataframe(df_review, use_container_width=True, hide_index=True, column_config=get_column_config())
        
        csv = df_review.to_csv(index=False)
        st.download_button("📥 Download Review List", data=csv, file_name="cin7_review_orders.csv", mime="text/csv")
        st.divider()
    
    # -------------------------------------------------------------------------
    # SKIP TABLE
    # -------------------------------------------------------------------------
    with st.expander(f"⏭️ Would Skip ({len(to_skip)} orders)"):
        st.caption("Skipped due to: Retail segment, Shopify Retail source, non-importable status (Draft/Pending/Cancelled), or $0 filter")
        if to_skip:
            df_skip = pd.DataFrame([order_to_summary(o) for o in to_skip])
            st.dataframe(df_skip, use_container_width=True, hide_index=True, column_config=get_column_config())
        else:
            st.info("No skipped orders")
    
    # -------------------------------------------------------------------------
    # SOURCE BREAKDOWN
    # -------------------------------------------------------------------------
    with st.expander("📊 Source Breakdown"):
        source_data = {}
        for o in orders:
            src = o.get('source', 'Unknown')
            seg = o.get('_segment', 'Unknown')
            key = (src, seg)
            if key not in source_data:
                source_data[key] = {'Source': src, 'Segment': seg, 'Count': 0, 'Revenue': 0}
            source_data[key]['Count'] += 1
            source_data[key]['Revenue'] += o.get('total', 0) or 0
        
        df_source = pd.DataFrame(source_data.values())
        if not df_source.empty:
            df_source = df_source.sort_values('Count', ascending=False)
            st.dataframe(
                df_source, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    'Revenue': st.column_config.NumberColumn('Revenue', format='$%.2f')
                }
            )
    
    # -------------------------------------------------------------------------
    # STATUS BREAKDOWN
    # -------------------------------------------------------------------------
    with st.expander("📋 Status Breakdown"):
        status_data = {}
        for o in orders:
            status = o.get('stage') or o.get('status') or 'Unknown'
            seg = o.get('_segment', 'Unknown')
            key = (status, seg)
            if key not in status_data:
                status_data[key] = {'Status': status, 'Segment': seg, 'Count': 0, 'Revenue': 0}
            status_data[key]['Count'] += 1
            status_data[key]['Revenue'] += o.get('total', 0) or 0
        
        df_status = pd.DataFrame(status_data.values())
        if not df_status.empty:
            df_status = df_status.sort_values('Count', ascending=False)
            st.dataframe(
                df_status, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    'Revenue': st.column_config.NumberColumn('Revenue', format='$%.2f')
                }
            )
            
            st.caption("✅ **Importable statuses**: Approved, Voided")
            st.caption("❌ **Skipped statuses**: Draft, Pending, Cancelled, and all others")
    
    st.divider()
    st.caption("🔄 **Cin7Connect Demo** — Read-only preview of what would sync to HubSpot")

if __name__ == "__main__":
    main()
