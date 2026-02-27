import os
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, HTTPException
from fastapi.openapi.models import APIKey

from app.caddy.caddy import caddy_server
from app.security import get_api_key
from app.utils import (
    check_a_record,
    check_txt_record,
    generate_random_string,
    get_a_records,
    silent_remove_file,
)

"""
Domain API
===========
GET     /domains
POST    /domains?domain=<domain>&upstream=<upstream>
DELETE  /domains?domain=<domain>
GET     /domains/verify/{domain}
"""

current_directory = os.getcwd()
dir_path = "domains/texts/"
texts_dir = os.path.join(current_directory, dir_path)
directory = os.path.dirname(texts_dir)
if not os.path.exists(directory):
    os.makedirs(directory)

domain_api = APIRouter()


@domain_api.get("/domains", tags=["Custom Domain API"])
async def get_domains(api_key: APIKey = Depends(get_api_key)):
    return caddy_server.list_domains()


@domain_api.post("/domains", tags=["Custom Domain API"])
async def add_domain(
    domain: str,
    upstream: Optional[str] = None,
    api_key: APIKey = Depends(get_api_key),
):
    caddy_server.add_custom_domain(domain, upstream)
    return "OK"


@domain_api.delete("/domains", tags=["Custom Domain API"])
async def remove_domains(
    domain: str,
    api_key: APIKey = Depends(get_api_key),
):
    caddy_server.remove_custom_domain(domain)
    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    silent_remove_file(filepath)
    return "OK"


@domain_api.get("/domains/verify/{domain}", tags=["Domain Verification API"])
async def verify_domain(domain: str, api_key: APIKey = Depends(get_api_key)):
    """Check whether *domain* has the correct A and TXT records."""
    server_ip = caddy_server.server_ip
    if not server_ip:
        raise HTTPException(status_code=500, detail="Server IP not available.")

    # --- TXT verification token (persisted per domain) ---
    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    if os.path.exists(filepath):
        async with aiofiles.open(filepath, mode="r") as file:
            txt_token = (await file.read()).strip()
    else:
        txt_token = generate_random_string()
        async with aiofiles.open(filepath, mode="w") as file:
            await file.write(txt_token)

    a_ok = check_a_record(domain, server_ip)
    txt_ok = check_txt_record(domain, txt_token)
    resolved_ips = get_a_records(domain)

    return {
        "server_ip": server_ip,
        "records": [
            {
                "type": "A",
                "expected": server_ip,
                "resolved": resolved_ips,
                "verified": a_ok,
            },
            {
                "type": "TXT",
                "expected": txt_token,
                "verified": txt_ok,
            },
        ],
        "a_verified": a_ok,
        "txt_verified": txt_ok,
    }
