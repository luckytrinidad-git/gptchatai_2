import streamlit as st
import pandas as pd
import os
import sys
import django
from datetime import datetime
import requests
from config import (
    API_URL,
    API_KEY
)

GENERAL_API_URL = f"{API_URL}/general"
# Helper to hide the "Running..." man
try:
    from ui_utils import hide_running_man
except ImportError:
    def hide_running_man(): pass

# =========================
# 2. UI CONFIGURATION
# =========================
# set_page_config MUST be the first streamlit command
st.set_page_config(page_title="Audit Log - SEC AI System", layout="wide")
hide_running_man()

# =========================
# 3. UI HEADER
# =========================
st.title("System Audit Log")
st.markdown("Official chronological record of system activities and data modifications.")

# =========================
# 4. DATA FETCHING & LOGIC
# =========================

try:
    response = requests.get(
        f"{GENERAL_API_URL}/audit_log",
        headers={
            "X-API-Key": API_KEY
        },
        timeout=30
    )
    response.raise_for_status()

    data = response.json()

    if data:
        df = pd.DataFrame(data)

        # Make sure Timestamp is treated as datetime
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(
                df["timestamp"],
                errors="coerce"
            )

        # =========================
        # SUMMARY
        # =========================
        total_logs = len(df)

        success_count = (
            df["status"]
            .astype(str)
            .str.lower()
            .eq("success")
            .sum()
        )

        fail_count = total_logs - success_count

        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                "Total Logs",
                total_logs
            )

        with col2:
            st.metric(
                "Successful",
                success_count
            )

        with col3:
            st.metric(
                "Failed",
                fail_count
            )

        st.divider()

        # =========================
        # FILTERS
        # =========================
        col1, col2, col3 = st.columns(3)

        with col1:
            selected_user = st.multiselect(
                "Filter by User",
                options=sorted(
                    df["username"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )
            )

        with col2:
            selected_status = st.multiselect(
                "Filter by Status",
                options=sorted(
                    df["status"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )
            )

        with col3:
            selected_module = st.multiselect(
                "Filter by Module",
                options=sorted(
                    df["module"]
                    .dropna()
                    .astype(str)
                    .unique()
                    .tolist()
                )
            )

        # =========================
        # APPLY FILTERS
        # =========================
        filtered_df = df.copy()

        if selected_user:
            filtered_df = filtered_df[
                filtered_df["username"].isin(selected_user)
            ]

        if selected_status:
            filtered_df = filtered_df[
                filtered_df["status"].isin(selected_status)
            ]

        if selected_module:
            filtered_df = filtered_df[
                filtered_df["module"].isin(selected_module)
            ]

        # =========================
        # DOWNLOAD CSV
        # =========================
        csv_data = filtered_df.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name="audit_logs.csv",
            mime="text/csv",
            use_container_width=True
        )

        st.divider()

        # =========================
        # AUDIT LOG TABLE
        # =========================
        st.dataframe(
            filtered_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn(
                    "Date & Time",
                    format="D MMM YYYY, h:mm a"
                ),
                "username": st.column_config.TextColumn(
                    "Actor"
                ),
                "action": st.column_config.TextColumn(
                    "Action"
                ),
                "module": st.column_config.TextColumn(
                    "Module"
                ),
                "status": st.column_config.TextColumn(
                    "Status"
                ),
            }
        )

        # =========================
        # REFRESH
        # =========================
        if st.button(
            "Refresh Logs",
            use_container_width=True
        ):
            st.rerun()

    else:
        st.info(
            "No audit logs recorded in the database yet."
        )

except requests.exceptions.RequestException as e:
    st.error(
        f"Unable to retrieve audit logs: {e}"
    )

except Exception as e:
    st.error(
        f"Error processing logs: {e}"
    )