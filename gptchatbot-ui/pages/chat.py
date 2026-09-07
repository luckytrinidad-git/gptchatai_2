import streamlit as st
import requests
import time
import json
from ui_utils import hide_running_man
from logger_utils import log_action
from config import (
    API_URL,
    API_KEY
)

st.set_page_config(page_title="Chat Assistant", layout="wide")
hide_running_man() 

# =========================
# CONFIG & ENDPOINTS
# =========================
INGEST_API_URL = f"{API_URL}/rag/ingest-knowledge"
GENERAL_API_URL = f"{API_URL}/general"

ENDPOINTS = {
    "openai": API_URL + "/openai/ask-openai",
    "internal": API_URL + "/rag/ask-bir",
    "revie": API_URL + "/revie/ask-revie",
    "sec": API_URL + "/sec/ask-sec",
}

# =========================
# INITIALIZATION
# =========================
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("Chat Assistant")

# =========================
# SIDEBAR SETTINGS
# =========================
with st.sidebar:
    with st.container():
        
        # response = requests.get(f"{GENERAL_API_URL}/agents",
        # headers={
        #     "X-API-Key": API_KEY
        # })
        # agents = response.json()
        # agents = [
        #     {"id": -1, "agent": "General"},
        # ] + agents
        
        agents = [
            {"id":5, "agent":"SEC"}
        ]
        
        selected_agent = st.selectbox(
            "Agent",
            options=agents,
            format_func=lambda x: x["agent"],
        )

        model = selected_agent["agent"]
        model_id = selected_agent["id"]

    # UPLOAD VISIBLE ONLY FOR EXTERNAL SOURCE

    uploaded_file = None
    if model == "General":
        uploaded_file = st.file_uploader(
            "Document Upload (General agent only)", 
            type=["pdf", "csv", "txt", "xlsx", "docx"],
        )
        if uploaded_file:
            st.info(f"Document successfully indexed.")

    if st.button("Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# =========================
# CHAT INTERFACE
# =========================

# 1. Redraw history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 2. Handle new User Input
if prompt := st.chat_input("Ask about anything..."):
    
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    with st.chat_message("user"):
        st.markdown(prompt)

    # 3. Process Assistant Response
    with st.chat_message("assistant"):
        with st.spinner(f"Agent {model} is thinking..."):
            try:
                # files = None

                # --- NEW ENDPOINT LOGIC WITH TERMINAL PRINTS ---
                if model == "Revie":
                    endpoint = ENDPOINTS["revie"]
                    print(f"\n[DEBUG] Model Selected: {model}")
                    print(f"[DEBUG] Calling Endpoint: {endpoint}")
                    
                    payload = {
                        "prompt": prompt,
                        "agent": model,
                        "history": json.dumps(st.session_state.messages[-10:])
                    }
                    response = requests.post(endpoint, 
                    headers={
                        "X-API-Key": API_KEY
                    },json=payload, timeout=180)
                    
                elif model == "SEC":
                    endpoint = ENDPOINTS["sec"]
                    print(f"\n[DEBUG] Model Selected: {model}")
                    print(f"[DEBUG] Calling Endpoint: {endpoint}")
                    
                    payload = {
                        "prompt": prompt,
                        "agent": model,
                        "history": json.dumps(st.session_state.messages[-10:])
                    }
                    response = requests.post(endpoint, 
                    headers={
                        "X-API-Key": API_KEY
                    },json=payload, timeout=180)
                    
                # --- CASE 2: EXTERNAL SOURCE ---
                elif model == "General":
                    endpoint = ENDPOINTS["openai"]
                    payload = {
                        "prompt": prompt, 
                        "agent": model, 
                        "history": json.dumps(st.session_state.messages[-10:])
                    }
    
                    if uploaded_file:
                        # Standard file upload request
                        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                        response = requests.post(endpoint, 
                        headers={
                            "X-API-Key": API_KEY
                        },data=payload, files=files, timeout=180)
                    else:
                        try:
                            response = requests.post(endpoint, 
                        headers={
                            "X-API-Key": API_KEY
                        },data=payload, timeout=180)
                        except Exception:
                        # Fallback for strict backends:
                            response = requests.post(endpoint, 
                            headers={
                                "X-API-Key": API_KEY
                            },data=payload, files={'file': ('', b'')}, timeout=180)

                else:
                    endpoint = ENDPOINTS["internal"]
                    print(f"\n[DEBUG] Model Selected: {model}")
                    print(f"[DEBUG] Calling Endpoint: {endpoint}")
                    
                    payload = {
                        "prompt": prompt,
                        "agent": model_id,
                        "history": json.dumps(st.session_state.messages[-10:])
                    }
                    response = requests.post(endpoint, 
                        headers={
                            "X-API-Key": API_KEY
                        },data=payload, timeout=180)
                
                # 4. ROBUST RESPONSE PARSING
                if response.status_code == 200:
                    res_json = response.json()
                    text = (
                        res_json.get("answer") or 
                        res_json.get("response") or 
                        res_json.get("output") or 
                        res_json.get("text") or 
                        "Error: Response format not recognized."
                    )
                    status_log = "success"
                else:
                    text = f"API Error {response.status_code}: {response.text}"
                    status_log = "failed"
            
            except Exception as e:
                text = f"Connection error: {e}"
                status_log = "error"

        # 5. Typewriter Effect
        placeholder = st.empty()
        full_res = ""
        words = text.split(" ")
        for chunk in words:
            full_res += chunk + " "
            placeholder.markdown(full_res + "▌")
            time.sleep(0.02)
        placeholder.markdown(full_res)

        # 6. Log Action
        log_action(
            username="End User", 
            action=f"Queried Agent: {model}", 
            module="Chat Assistant",
            status=status_log
        )

        # 7. Save Assistant Response and Rerun
        st.session_state.messages.append({"role": "assistant", "content": text})
        st.rerun()