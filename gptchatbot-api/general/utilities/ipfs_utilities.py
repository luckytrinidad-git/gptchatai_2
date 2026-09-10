import requests
from gptchatbot.settings import IPFS_SERVER_URL

def upload_to_ipfs(file_name, file_bytes, content_type):
    files = {
        "file": (
            file_name,
            file_bytes,
            content_type or "application/octet-stream"
        )
    }

    response = requests.post(
        f"{IPFS_SERVER_URL}/add",
        files=files,
        params={"pin": "true"},
        timeout=(10, 300),
    )

    result = response.json()

    cid = result["Hash"]

    return cid


def download_from_ipfs(file_cid):
    if not file_cid:
        raise ValueError(
            "Missing IPFS CID."
        )

    try:
        response = requests.get(
            f"{IPFS_SERVER_URL}/ipfs/{file_cid}",
            timeout=120,
        )

        response.raise_for_status()
        return response.content

    except requests.exceptions.RequestException as e:
        raise Exception(
            f"Failed downloading file from IPFS: {str(e)}"
        )