import streamlit as st
import pandas as pd
import requests
import time
import os
from logger_utils import log_action
FILE_EXTENSIONS = [
    ext.strip().lower()
    for ext in os.getenv(
        "FILE_EXTENSIONS",
        ""
    ).split(",")
    if ext.strip()
]

# =========================
# 2. CONFIG & ENDPOINTS
# =========================
from config import (
    API_URL,
    API_KEY
)
INGEST_API_URL = f"{API_URL}/rag/ingest-knowledge"
REVIE_INGEST_URL = f"{API_URL}/revie/intents/import"
SEC_INGEST_URL = f"{API_URL}/sec/ingest-knowledge"
GENERAL_API_URL = f"{API_URL}/general"

st.title("KX Topics: Knowledge Manager")

# =========================
# 3. INGESTION FORM
# =========================
with st.expander("Ingest New Document", expanded=True):
    with st.form("kx_form", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            title = st.text_input("Topic Title")
        with col2:
            selected_agent = st.selectbox(
                "Agent Responsible",
                options=[
                    {
                        "id": 5,
                        "agent": "SEC"
                    }
                ],
                format_func=lambda x: x["agent"]
            )

            agent_id = selected_agent["id"]
            agent_name = selected_agent["agent"]

        with col3:
            uploaded_by = st.text_input("Uploaded By", value="Admin")
        
        # =========================
        # SEC INFORMATION
        # =========================

        col1, col2, col3, col4 = st.columns([2, 1.5, 1.5, 1])

        with col1:
            company_name = st.text_input(
                "Company Name",
                placeholder="e.g. Ayala Corporation"
            )

        with col2:
            sec_no = st.text_input(
                "SEC No.",
                placeholder="e.g. 123456"
            )

        with col3:
            form_type = st.text_input(
                "Form Type",
                placeholder="e.g. 17-A"
            )

        with col4:
            period_covered = st.text_input(
                "Period Covered",
                placeholder="e.g. 2025"
            )
        up_file = st.file_uploader("Upload Source", type=FILE_EXTENSIONS)
        submit = st.form_submit_button("Upload & Process", use_container_width=True, type="primary")

        if submit:
            print("SUBMIT CLICKED")
            
            if not title:
                st.error("Topic Title is required.")
            elif not up_file:
                st.error("File is required.")
            else:
                with st.status("Processing...", expanded=True) as status:
                    try:
                        print("posting")

                        files = {
                            "file": (
                                up_file.name,
                                up_file.getvalue(),
                                up_file.type
                            )
                        }

                        payload = {
                            "title": title,
                            "agent": str(agent_id),
                            "uploaded_by": uploaded_by,
                            "company_name": company_name,
                            "sec_no": sec_no,
                            "form_type": form_type,
                            "period_covered": period_covered,
                        }

                        print("POST URL:", SEC_INGEST_URL)
                        print("PAYLOAD:", payload)

                        response = requests.post(
                            SEC_INGEST_URL,
                            headers={
                                "X-API-Key": API_KEY
                            },
                            data=payload,
                            files=files,
                            timeout=300
                        )

                        print("STATUS:", response.status_code)
                        print("RESPONSE:", response.text)

                        try:
                            result = response.json()
                        except ValueError:
                            result = {
                                "status": "error",
                                "message": response.text
                            }

                        if result.get("status") == "success":
                            log_action(
                                action="Upload",
                                status="Success",
                                details=f"Successfully processed: {title}"
                            )

                            status.update(
                                label="Upload Complete!",
                                state="complete"
                            )

                            st.success(f"Successfully processed: {title}")

                            time.sleep(1)
                            st.rerun()

                        else:
                            # API returned an error, even if HTTP status is 200
                            error_message = result.get(
                                "message",
                                "Unknown ingestion error."
                            )

                            status.update(
                                label="Upload Failed",
                                state="error"
                            )

                            st.error(f"Ingestion Error: {error_message}")

                            log_action(
                                action="Upload",
                                status="Failed",
                                details=error_message
                            )

                    except Exception as e:
                        status.update(
                            label="Connection Error",
                            state="error"
                        )

                        st.error(f"Connection Error: {e}")

# =========================
# 4. VIEW REPOSITORY
# =========================
st.subheader("Unified Knowledge Repository")
response = requests.get(f"{GENERAL_API_URL}/topics",
        headers={
            "X-API-Key": API_KEY
        })

if response.status_code == 200:
    view_df = pd.DataFrame(response.json())
    st.dataframe(
        view_df,
        use_container_width=True,
        hide_index=True
    )
else:
    st.error(f"Failed to load repository: {response.text}")