"""
Cin7Connect — Cin7 to HubSpot Order Sync
========================================
- Fetches wholesale orders from Cin7
- Matches to HubSpot contacts/companies (email first, company name fallback)
- Push selected orders to HubSpot as Closed Won deals
- Exceptions section for unmatched/problem orders
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
from thefuzz import fuzz

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
GENERIC_EMAIL_DOMAINS = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com', 'icloud.com', 'mail.com', 'protonmail.com']
FUZZY_MATCH_THRESHOLD = 80

# =============================================================================
# SESSION STATE INIT
# =============================================================================
defaults = {
    'cin7_connected': False,
    'hubspot_connected': False,
    'fetched_orders': None,
    'fetch_since': None,
    'fetch_until': None,
    'hubspot_companies': None,
    'hubspot_contacts': None,
    'matched_orders': [],
    'exceptions': {},
    'selected_orders': set(),
    'push_results': None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# =============================================================================
# CREDENTIALS (from Streamlit Secrets or manual input)
# =============================================================================
def get_credentials():
    """Get credentials from secrets or session state."""
    cin7_user = st.secrets.get("CIN7_USERNAME", "") if hasattr(st, 'secrets') else ""
    cin7_key = st.secrets.get("CIN7_API_KEY", "") if hasattr(st, 'secrets') else ""
    hubspot_key = st.secrets.get("HUBSPOT_API_KEY", "") if hasattr(st, 'secrets') else ""
    return cin7_user, cin7_key, hubspot_key

# =============================================================================
# WHOLESALE VS RETAIL DETECTION
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
def test_cin7_connection(username: str, api_key: str) -> tuple:
    """Test Cin7 connection."""
    try:
        response = requests.get(
            "https://api.cin7.com/api/v1/SalesOrders",
            auth=(username, api_key),
            params={"rows": 1},
            timeout=30
        )
        if response.status_code == 200:
            return True, "✅ Connected to Cin7!"
        elif response.status_code == 401:
            return False, "❌ Invalid credentials"
        else:
            return False, f"❌ Error {response.status_code}"
    except Exception as e:
        return False, f"❌ Connection error: {str(e)}"

def fetch_cin7_orders(username: str, api_key: str, since: datetime, until: datetime) -> list:
    """Fetch orders from Cin7."""
    start_str = since.strftime("%Y-%m-%dT00:00:00Z")
    end_str = until.strftime("%Y-%m-%dT23:59:59Z")
    
    all_orders = []
    page = 1
    
    while True:
        response = requests.get(
            "https://api.cin7.com/api/v1/SalesOrders",
            auth=(username, api_key),
            params={
                "where": f"createdDate >= '{start_str}' AND createdDate <= '{end_str}'",
                "page": page,
                "rows": 250
            },
            timeout=60
        )
        if response.status_code != 200:
            break
        orders = response.json()
        if not orders:
            break
        all_orders.extend(orders)
        if len(orders) < 250:
            break
        page += 1
    
    # Classify each order
    for o in all_orders:
        o['_segment'] = classify_order(o)
    
    return all_orders

# =============================================================================
# HUBSPOT API
# =============================================================================
def test_hubspot_connection(api_key: str) -> tuple:
    """Test HubSpot connection."""
    try:
        response = requests.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"limit": 1},
            timeout=30
        )
        if response.status_code == 200:
            return True, "✅ Connected to HubSpot!"
        elif response.status_code == 401:
            return False, "❌ Invalid API key"
        else:
            return False, f"❌ Error {response.status_code}"
    except Exception as e:
        return False, f"❌ Connection error: {str(e)}"

def fetch_hubspot_contacts(api_key: str) -> list:
    """Fetch all contacts from HubSpot with email and company associations."""
    all_contacts = []
    after = None
    
    while True:
        params = {
            "limit": 100,
            "properties": "email,firstname,lastname,company",
        }
        if after:
            params["after"] = after
        
        response = requests.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
            timeout=60
        )
        
        if response.status_code != 200:
            break
        
        data = response.json()
        contacts = data.get("results", [])
        all_contacts.extend(contacts)
        
        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")
        if not after:
            break
    
    return all_contacts

def fetch_hubspot_companies(api_key: str) -> list:
    """Fetch all companies from HubSpot."""
    all_companies = []
    after = None
    
    while True:
        params = {
            "limit": 100,
            "properties": "name,domain",
        }
        if after:
            params["after"] = after
        
        response = requests.get(
            "https://api.hubapi.com/crm/v3/objects/companies",
            headers={"Authorization": f"Bearer {api_key}"},
            params=params,
            timeout=60
        )
        
        if response.status_code != 200:
            break
        
        data = response.json()
        companies = data.get("results", [])
        all_companies.extend(companies)
        
        paging = data.get("paging", {})
        after = paging.get("next", {}).get("after")
        if not after:
            break
    
    return all_companies

def check_deal_exists(api_key: str, cin7_order_id: str) -> bool:
    """Check if a deal with this Cin7 order ID already exists."""
    response = requests.post(
        "https://api.hubapi.com/crm/v3/objects/deals/search",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json={
            "filterGroups": [{
                "filters": [{
                    "propertyName": "cin7_order_id",
                    "operator": "EQ",
                    "value": cin7_order_id
                }]
            }],
            "limit": 1
        },
        timeout=30
    )
    
    if response.status_code == 200:
        results = response.json().get("results", [])
        return len(results) > 0
    return False

def create_hubspot_deal(api_key: str, order: dict, contact_id: str = None, company_id: str = None) -> tuple:
    """Create a deal in HubSpot for the order."""
    order_ref = order.get('reference', '')
    order_total = order.get('total', 0) or 0
    order_date = (order.get('createdDate') or '')[:10]
    company_name = order.get('company') or order.get('billingCompany') or ''
    
    # Deal properties
    deal_data = {
        "properties": {
            "dealname": f"{company_name} - {order_ref}",
            "amount": str(order_total),
            "dealstage": "closedwon",
            "closedate": order_date,
            "cin7_order_id": order_ref,
            "pipeline": "default"
        }
    }
    
    # Create deal
    response = requests.post(
        "https://api.hubapi.com/crm/v3/objects/deals",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json=deal_data,
        timeout=30
    )
    
    if response.status_code != 201:
        return False, f"Failed to create deal: {response.text}"
    
    deal_id = response.json().get("id")
    
    # Associate with contact
    if contact_id:
        requests.put(
            f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}/associations/contacts/{contact_id}/deal_to_contact",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30
        )
    
    # Associate with company
    if company_id:
        requests.put(
            f"https://api.hubapi.com/crm/v3/objects/deals/{deal_id}/associations/companies/{company_id}/deal_to_company",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30
        )
    
    return True, deal_id

# =============================================================================
# MATCHING LOGIC
# =============================================================================
def get_email_domain(email: str) -> str:
    """Extract domain from email."""
    if not email or '@' not in email:
        return ''
    return email.split('@')[1].lower()

def match_order_to_hubspot(order: dict, contacts: list, companies: list) -> dict:
    """
    Match a Cin7 order to HubSpot contact/company.
    Returns: {matched: bool, contact_id, company_id, match_type, exception_type, exception_reason}
    """
    order_email = (order.get('email') or order.get('memberEmail') or '').strip().lower()
    order_company = (order.get('company') or order.get('billingCompany') or '').strip()
    order_ref = order.get('reference', '')
    
    result = {
        'matched': False,
        'contact_id': None,
        'company_id': None,
        'match_type': None,
        'exception_type': None,
        'exception_reason': None,
    }
    
    # Check for missing email
    if not order_email:
        result['exception_type'] = 'missing_email_cin7'
        result['exception_reason'] = 'Order has no email address in Cin7'
        return result
    
    # Check for generic email
    email_domain = get_email_domain(order_email)
    if email_domain in GENERIC_EMAIL_DOMAINS and not order_company:
        result['exception_type'] = 'generic_email'
        result['exception_reason'] = f'Generic email ({email_domain}) with no company name'
        return result
    
    # STEP 1: Try exact email match
    for contact in contacts:
        contact_email = (contact.get('properties', {}).get('email') or '').lower()
        if contact_email == order_email:
            result['matched'] = True
            result['contact_id'] = contact.get('id')
            result['match_type'] = 'email_exact'
            # Try to find associated company
            for company in companies:
                company_domain = (company.get('properties', {}).get('domain') or '').lower()
                if company_domain and company_domain == email_domain:
                    result['company_id'] = company.get('id')
                    break
            return result
    
    # STEP 2: Try fuzzy company name match
    if order_company:
        best_match = None
        best_score = 0
        
        for company in companies:
            company_name = company.get('properties', {}).get('name') or ''
            if not company_name:
                continue
            
            score = fuzz.ratio(order_company.lower(), company_name.lower())
            if score > best_score:
                best_score = score
                best_match = company
        
        if best_score >= FUZZY_MATCH_THRESHOLD:
            result['matched'] = True
            result['company_id'] = best_match.get('id')
            result['match_type'] = f'company_fuzzy_{best_score}%'
            return result
        elif best_score >= 60:
            # Ambiguous match
            result['exception_type'] = 'ambiguous_match'
            result['exception_reason'] = f'Best company match "{best_match.get("properties", {}).get("name")}" scored {best_score}% (threshold: {FUZZY_MATCH_THRESHOLD}%)'
            return result
    
    # No match found
    result['exception_type'] = 'no_match'
    result['exception_reason'] = f'Email "{order_email}" not found, company "{order_company}" did not match'
    return result

# =============================================================================
# DISPLAY HELPERS
# =============================================================================
def order_to_display(order: dict, match_result: dict = None) -> dict:
    """Convert order to display format."""
    total = order.get('total', 0) or 0
    display = {
        'Order #': order.get('reference', ''),
        'Company': order.get('company') or order.get('billingCompany') or '',
        'Email': order.get('email') or order.get('memberEmail') or '',
        'Total': f"${total:,.2f}",
        'Date': (order.get('createdDate') or '')[:10],
        'Source': order.get('source', ''),
    }
    if match_result:
        display['Match Type'] = match_result.get('match_type') or 'N/A'
    return display

# =============================================================================
# MAIN APP
# =============================================================================
def main():
    st.title("🔄 Cin7Connect")
    st.markdown("**Cin7 → HubSpot Order Sync**")
    
    st.divider()
    
    # =========================================================================
    # SIDEBAR
    # =========================================================================
    with st.sidebar:
        st.header("🔌 Connections")
        
        # Get any secrets
        secret_cin7_user, secret_cin7_key, secret_hubspot_key = get_credentials()
        
        # Cin7
        st.subheader("Cin7 Omni")
        cin7_user = st.text_input("Username", value=secret_cin7_user, key="cin7_user_input")
        cin7_key = st.text_input("API Key", type="password", value=secret_cin7_key, key="cin7_key_input")
        
        if st.button("Test Cin7", use_container_width=True):
            if cin7_user and cin7_key:
                success, message = test_cin7_connection(cin7_user, cin7_key)
                if success:
                    st.session_state.cin7_connected = True
                    st.success(message)
                else:
                    st.session_state.cin7_connected = False
                    st.error(message)
            else:
                st.error("Enter username and API key")
        
        st.caption(f"Status: {'✅ Connected' if st.session_state.cin7_connected else '❌ Not connected'}")
        
        st.divider()
        
        # HubSpot
        st.subheader("HubSpot")
        hubspot_key = st.text_input("Private App Token", type="password", value=secret_hubspot_key, key="hubspot_key_input")
        
        if st.button("Test HubSpot", use_container_width=True):
            if hubspot_key:
                success, message = test_hubspot_connection(hubspot_key)
                if success:
                    st.session_state.hubspot_connected = True
                    st.success(message)
                else:
                    st.session_state.hubspot_connected = False
                    st.error(message)
            else:
                st.error("Enter API key")
        
        st.caption(f"Status: {'✅ Connected' if st.session_state.hubspot_connected else '❌ Not connected'}")
        
        st.divider()
        
        # Filters
        st.subheader("⚙️ Filters")
        exclude_zero = st.checkbox("Exclude $0.00 orders", value=True)
        
        st.divider()
        st.caption("🔒 Credentials are not stored")
    
    # =========================================================================
    # MAIN CONTENT - NOT CONNECTED
    # =========================================================================
    if not st.session_state.cin7_connected or not st.session_state.hubspot_connected:
        st.warning("👈 Connect to both Cin7 and HubSpot using the sidebar")
        return
    
    # =========================================================================
    # DATE RANGE + FETCH
    # =========================================================================
    st.subheader("📅 Select Date Range")
    col1, col2 = st.columns(2)
    with col1:
        since_date = st.date_input("From", value=datetime.now() - timedelta(days=7))
    with col2:
        until_date = st.date_input("To", value=datetime.now())
    
    since = datetime.combine(since_date, datetime.min.time())
    until = datetime.combine(until_date, datetime.max.time())
    
    # Fetch button
    if st.button("🔍 Fetch Orders & Match to HubSpot", use_container_width=True, type="primary"):
        
        # Fetch orders
        with st.spinner("Fetching orders from Cin7..."):
            orders = fetch_cin7_orders(cin7_user, cin7_key, since, until)
        
        if not orders:
            st.warning("No orders found in this date range")
            return
        
        # Filter for wholesale only
        wholesale_orders = [o for o in orders if o.get('_segment') == 'Wholesale']
        retail_orders = [o for o in orders if o.get('_segment') == 'Retail']
        
        # Exclude $0 if checked
        if exclude_zero:
            wholesale_orders = [o for o in wholesale_orders if (o.get('total') or 0) > 0]
        
        st.session_state.fetched_orders = orders
        st.session_state.fetch_since = since_date
        st.session_state.fetch_until = until_date
        
        # Fetch HubSpot data
        with st.spinner("Fetching contacts from HubSpot..."):
            contacts = fetch_hubspot_contacts(hubspot_key)
            st.session_state.hubspot_contacts = contacts
        
        with st.spinner("Fetching companies from HubSpot..."):
            companies = fetch_hubspot_companies(hubspot_key)
            st.session_state.hubspot_companies = companies
        
        # Match orders
        with st.spinner("Matching orders to HubSpot..."):
            matched = []
            exceptions = {
                'no_match': [],
                'ambiguous_match': [],
                'generic_email': [],
                'missing_email_cin7': [],
                'duplicate': [],
            }
            
            for order in wholesale_orders:
                order_ref = order.get('reference', '')
                
                # Check for duplicate
                if check_deal_exists(hubspot_key, order_ref):
                    exceptions['duplicate'].append({
                        'order': order,
                        'reason': f'Order {order_ref} already exists in HubSpot'
                    })
                    continue
                
                # Try to match
                match_result = match_order_to_hubspot(order, contacts, companies)
                
                if match_result['matched']:
                    matched.append({
                        'order': order,
                        'match': match_result
                    })
                else:
                    exc_type = match_result.get('exception_type', 'no_match')
                    exceptions[exc_type].append({
                        'order': order,
                        'reason': match_result.get('exception_reason', 'Unknown')
                    })
            
            st.session_state.matched_orders = matched
            st.session_state.exceptions = exceptions
            st.session_state.selected_orders = set(range(len(matched)))  # Select all by default
        
        st.success(f"Fetched {len(orders)} orders, {len(wholesale_orders)} wholesale, {len(matched)} matched!")
    
    # =========================================================================
    # RESULTS
    # =========================================================================
    orders = st.session_state.fetched_orders
    matched = st.session_state.matched_orders
    exceptions = st.session_state.exceptions
    
    if orders is None:
        return
    
    wholesale_orders = [o for o in orders if o.get('_segment') == 'Wholesale']
    retail_orders = [o for o in orders if o.get('_segment') == 'Retail']
    
    st.caption(f"📦 **{len(orders)} orders loaded** from {st.session_state.fetch_since} to {st.session_state.fetch_until}")
    
    st.divider()
    
    # =========================================================================
    # METRICS
    # =========================================================================
    st.subheader("📊 Results — Wholesale Orders")
    
    total_exceptions = sum(len(v) for v in exceptions.values())
    matched_total = sum((m['order'].get('total') or 0) for m in matched)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Wholesale Orders", len(wholesale_orders))
    with col2:
        st.metric("Ready to Push", len(matched), delta=f"${matched_total:,.0f}")
    with col3:
        st.metric("Exceptions", total_exceptions)
    with col4:
        st.metric("Total Revenue", f"${matched_total:,.0f}")
    
    st.divider()
    
    # =========================================================================
    # READY TO PUSH (with checkboxes)
    # =========================================================================
    st.subheader(f"✅ Ready to Push ({len(matched)} orders)")
    
    if matched:
        # Select all / Deselect all buttons
        col1, col2, col3 = st.columns([1, 1, 4])
        with col1:
            if st.button("Select All"):
                st.session_state.selected_orders = set(range(len(matched)))
                st.rerun()
        with col2:
            if st.button("Deselect All"):
                st.session_state.selected_orders = set()
                st.rerun()
        
        # Display orders with checkboxes
        for i, item in enumerate(matched):
            order = item['order']
            match = item['match']
            
            order_ref = order.get('reference', '')
            company = order.get('company') or order.get('billingCompany') or ''
            total = order.get('total', 0) or 0
            match_type = match.get('match_type', '')
            
            is_selected = i in st.session_state.selected_orders
            
            col1, col2 = st.columns([0.05, 0.95])
            with col1:
                if st.checkbox("", value=is_selected, key=f"order_{i}", label_visibility="collapsed"):
                    st.session_state.selected_orders.add(i)
                else:
                    st.session_state.selected_orders.discard(i)
            with col2:
                st.write(f"**{order_ref}** — {company} — ${total:,.2f} — *{match_type}*")
        
        st.divider()
        
        # Push button
        selected_count = len(st.session_state.selected_orders)
        selected_total = sum(
            (matched[i]['order'].get('total') or 0) 
            for i in st.session_state.selected_orders
        )
        
        if selected_count > 0:
            if st.button(
                f"🚀 PUSH {selected_count} ORDERS TO HUBSPOT (${selected_total:,.0f})",
                type="primary",
                use_container_width=True
            ):
                success_count = 0
                fail_count = 0
                errors = []
                
                progress = st.progress(0)
                status = st.empty()
                
                selected_list = sorted(st.session_state.selected_orders)
                
                for idx, i in enumerate(selected_list):
                    item = matched[i]
                    order = item['order']
                    match = item['match']
                    order_ref = order.get('reference', '')
                    
                    status.write(f"Pushing {order_ref}...")
                    
                    success, result = create_hubspot_deal(
                        hubspot_key,
                        order,
                        contact_id=match.get('contact_id'),
                        company_id=match.get('company_id')
                    )
                    
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                        errors.append(f"{order_ref}: {result}")
                    
                    progress.progress((idx + 1) / len(selected_list))
                
                progress.empty()
                status.empty()
                
                st.session_state.push_results = {
                    'success': success_count,
                    'failed': fail_count,
                    'errors': errors
                }
                
                if success_count > 0:
                    st.success(f"✅ Pushed {success_count} orders to HubSpot!")
                if fail_count > 0:
                    st.error(f"❌ {fail_count} orders failed")
                    for err in errors:
                        st.write(f"  - {err}")
        else:
            st.info("Select orders to push")
    else:
        st.info("No matched orders to push")
    
    st.divider()
    
    # =========================================================================
    # EXCEPTIONS SECTION
    # =========================================================================
    st.subheader("⚠️ Exceptions")
    
    if total_exceptions == 0:
        st.success("No exceptions!")
    else:
        # No Match - RED
        if exceptions.get('no_match'):
            with st.expander(f"🔴 No Match Found ({len(exceptions['no_match'])})"):
                st.error("These orders could not be matched to any HubSpot contact or company.")
                for item in exceptions['no_match']:
                    order = item['order']
                    st.write(f"**{order.get('reference')}** — {order.get('company', '')} — {order.get('email', '')} — {item['reason']}")
        
        # Ambiguous Match - ORANGE
        if exceptions.get('ambiguous_match'):
            with st.expander(f"🟠 Ambiguous Match ({len(exceptions['ambiguous_match'])})"):
                st.warning("These orders matched multiple companies or scored below the threshold.")
                for item in exceptions['ambiguous_match']:
                    order = item['order']
                    st.write(f"**{order.get('reference')}** — {order.get('company', '')} — {item['reason']}")
        
        # Generic Email - YELLOW
        if exceptions.get('generic_email'):
            with st.expander(f"🟡 Generic Email Only ({len(exceptions['generic_email'])})"):
                st.warning("These orders only have gmail/yahoo/etc. emails with no company name.")
                for item in exceptions['generic_email']:
                    order = item['order']
                    st.write(f"**{order.get('reference')}** — {order.get('email', '')} — {item['reason']}")
        
        # Missing Email - YELLOW
        if exceptions.get('missing_email_cin7'):
            with st.expander(f"🟡 Missing Email in Cin7 ({len(exceptions['missing_email_cin7'])})"):
                st.warning("These orders have no email address in Cin7.")
                for item in exceptions['missing_email_cin7']:
                    order = item['order']
                    st.write(f"**{order.get('reference')}** — {order.get('company', '')} — {item['reason']}")
        
        # Duplicate - BLUE
        if exceptions.get('duplicate'):
            with st.expander(f"🔵 Already in HubSpot ({len(exceptions['duplicate'])})"):
                st.info("These orders already exist in HubSpot (skipped to prevent duplicates).")
                for item in exceptions['duplicate']:
                    order = item['order']
                    st.write(f"**{order.get('reference')}** — {order.get('company', '')} — {item['reason']}")
        
        # Download exceptions
        all_exceptions = []
        for exc_type, items in exceptions.items():
            for item in items:
                order = item['order']
                all_exceptions.append({
                    'Type': exc_type,
                    'Order #': order.get('reference', ''),
                    'Company': order.get('company', ''),
                    'Email': order.get('email', ''),
                    'Total': order.get('total', 0),
                    'Reason': item.get('reason', '')
                })
        
        if all_exceptions:
            df_exc = pd.DataFrame(all_exceptions)
            csv = df_exc.to_csv(index=False)
            st.download_button(
                "📥 Download Exceptions CSV",
                data=csv,
                file_name=f"cin7connect_exceptions_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
    
    st.divider()
    
    # =========================================================================
    # RETAIL (collapsed)
    # =========================================================================
    with st.expander(f"▶ Retail Orders ({len(retail_orders)}) — click to expand"):
        if retail_orders:
            retail_data = [order_to_display(o) for o in retail_orders]
            st.dataframe(pd.DataFrame(retail_data), use_container_width=True, hide_index=True)
        else:
            st.info("No retail orders")
    
    # Footer
    st.divider()
    st.caption("🔄 **Cin7Connect** — Wholesale orders sync to HubSpot as Closed Won deals")

if __name__ == "__main__":
    main()
