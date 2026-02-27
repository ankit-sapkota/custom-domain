import os
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, HTTPException
from fastapi.openapi.models import APIKey

from app.caddy.caddy import caddy_server
from app.domain_queue import pending_queue
from app.security import get_api_key
from app.utils import (
    check_a_record,
    check_txt_record,
    generate_random_string,
    get_a_records,
    get_txt_records,
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
    verified = caddy_server.list_domains()
    all_queued = pending_queue.get_all()
    pending_list = []
    failed_list = []
    for domain, info in all_queued.items():
        entry = {
            "domain": domain,
            "upstream": info.get("upstream"),
            "added_at": info.get("added_at"),
            "status": info.get("status", "pending"),
        }
        if info.get("status") == "failed":
            failed_list.append(entry)
        else:
            pending_list.append(entry)
    return {
        "verified": verified,
        "pending": pending_list,
        "failed": failed_list,
    }


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
    # Remove from pending queue (if present)
    pending_queue.remove(domain)

    # Remove from live Caddy config (if present)
    if domain in caddy_server.list_domains():
        caddy_server.remove_custom_domain(domain)

    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    silent_remove_file(filepath)
    return "OK"


@domain_api.get("/domains/verify/{domain}", tags=["Domain Verification API"])
async def verify_domain(
    domain: str,
    upstream: Optional[str] = None,
    api_key: APIKey = Depends(get_api_key),
):
    """Check whether *domain* has the correct A and TXT records.

    Behaves like ``POST /domains`` when the domain is not yet tracked:
    it will be added to the pending queue automatically.  If a ``failed``
    domain is verified again, it is reset back to ``pending``.

    When both A and TXT records pass immediately, the domain is promoted
    into Caddy right away.
    """
    server_ip = caddy_server.server_ip
    if not server_ip:
        raise HTTPException(status_code=500, detail="Server IP not available.")

    resolved_upstream = upstream or caddy_server.saas_upstream
    already_verified = domain in caddy_server.list_domains()

    # --- Ensure the domain is tracked (enqueue if new) ---
    if not already_verified and not pending_queue.get_status(domain):
        caddy_server.add_custom_domain(domain, upstream)

    # --- If domain is failed, reset to pending so it gets re-checked ---
    if pending_queue.is_failed(domain):
        pending_queue.mark_pending(domain)

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
    resolved_txts = get_txt_records(domain)

    # --- If both records pass and domain is still pending, promote now ---
    if a_ok and txt_ok and not already_verified and pending_queue.is_pending(domain):
        if caddy_server.promote_domain(domain, resolved_upstream):
            pending_queue.remove(domain)

    # Determine queue status for the response
    queue_status = pending_queue.get_status(domain)
    if domain in caddy_server.list_domains():
        queue_status = "verified"

    return {
        "server_ip": server_ip,
        "queue_status": queue_status,
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
                "resolved": resolved_txts,
                "verified": txt_ok,
            },
        ],
        "domain_verified": a_ok,
        "txt_verified": txt_ok,
    }
