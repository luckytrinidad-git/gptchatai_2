import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from ui_utils import hide_running_man

st.set_page_config(page_title="Dashboard", layout="wide")
hide_running_man() 

# =========================
# DATA PREPARATION (MOCK DATA)
# =========================

# 1. Citizen's Charter - Top 5 Items (Inquiries/Usage)
charter_data = pd.DataFrame({
    "Charter Item": [
        "Income Tax Filing", 
        "VAT Registration", 
        "TIN Issuance", 
        "Estate Tax Settlement", 
        "Authority to Print"
    ],
    "Requests": [1250, 980, 850, 600, 450]
})

# 2. Regional Compliance Data (PH Map)
# Categorized into 5 Compliance levels
regional_data = pd.DataFrame({
    "Region": ["NCR", "CALABARZON", "Central Visayas", "Davao Region", "Ilocos Region"],
    "Lat": [14.5995, 14.1008, 10.3157, 7.1907, 18.1960],
    "Lon": [120.9842, 121.0794, 123.8854, 125.4553, 120.5927],
    "Compliance_Score": [98, 92, 88, 85, 82],
    "Category": ["Excellent", "Very High", "High", "Good", "Satisfactory"]
})

# =========================
# DASHBOARD UI
# =========================

st.title("Dashboard")
st.markdown("Real-time monitoring of SEC Citizen's Charter engagement and Regional Compliance.")

# --- TOP METRICS ROW ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Inquiries", "4,230", "+12%")
col2.metric("Avg. Compliance", "89%", "+5%")
col3.metric("Active Regions", "17", "Stable")
col4.metric("AI Accuracy", "94.2%", "+1.5%")

st.divider()

# --- MAIN CONTENT ROW ---
row2_col1, row2_col2 = st.columns([1, 1])

with row2_col1:
    st.subheader("Top 5 Citizen's Charter Items")
    st.caption("Most frequently accessed services via AI Assistant")
    
    fig_charter = px.bar(
        charter_data, 
        x="Requests", 
        y="Charter Item", 
        orientation='h',
        color="Requests",
        color_continuous_scale="Blues",
        template="plotly_white"
    )
    fig_charter.update_layout(showlegend=False, height=400, margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig_charter, use_container_width=True)

with row2_col2:
    st.subheader("Regional Filing Compliance")
    st.caption("Top 5 Regions by Filing Timeliness")
    
    # Philippine Map Visualization
    fig_map = px.scatter_mapbox(
        regional_data,
        lat="Lat",
        lon="Lon",
        size="Compliance_Score",
        color="Category",
        color_discrete_map={
            "Excellent": "#004a99",
            "Very High": "#1e88e5",
            "High": "#42a5f5",
            "Good": "#90caf9",
            "Satisfactory": "#e3f2fd"
        },
        hover_name="Region",
        hover_data=["Compliance_Score", "Category"],
        zoom=4.5,
        center={"lat": 12.8797, "lon": 121.7740},
        mapbox_style="carto-positron",
        height=400
    )
    fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig_map, use_container_width=True)

# --- RECENT FILING TRENDS ---
st.divider()
st.subheader("Monthly Filing Trend")
trend_data = pd.DataFrame({
    "Month": ["Jan", "Feb", "Mar", "Apr"],
    "Filing Volume": [4500, 5200, 8900, 12000] # April is tax month!
})
fig_trend = px.line(trend_data, x="Month", y="Filing Volume", markers=True)
fig_trend.update_traces(line_color='#004a99', line_width=3)
st.plotly_chart(fig_trend, use_container_width=True)