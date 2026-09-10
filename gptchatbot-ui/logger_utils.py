import streamlit as st
from django.db import connections
from datetime import datetime
import requests

from config import (
    API_URL,
    API_KEY
)

GENERAL_API_URL = f"{API_URL}/general"

def log_action(username, action, module, status="success"):
    
    payload = {
        "username": username, 
        "action": action, 
        "module": module, 
        "status": status.lower()
    }
    
    response = requests.post(
        f"{GENERAL_API_URL}/log_audit", 
        headers={
            "X-API-Key": API_KEY
        },
        json=payload,
        timeout=300
    )