import os
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.openapi.models import APIKey
from fastapi.responses import PlainTextResponse
from app.caddy.caddy import caddy_server
from app.security import get_api_key
from app.utils import generate_random_string
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
texts_dir  = os.path.join(current_directory, dir_path)
directory = os.path.dirname(texts_dir)
if not os.path.exists(directory):
    os.makedirs(directory)

domain_api = APIRouter()


@domain_api.get("/domains", tags=["Custom Domain API"])
async def get_domains(api_key: APIKey = Depends(get_api_key)):
    return caddy_server.list_domains()


@domain_api.post("/domains", tags=["Custom Domain API"])
async def add_domain(domain: str,
                     upstream: Optional[str] = None,
                     api_key: APIKey = Depends(get_api_key)):
    caddy_server.add_custom_domain(domain, upstream)
    return "OK"


@domain_api.delete("/domains", tags=["Custom Domain API"])
async def remove_domains(domain: str,
                         api_key: APIKey = Depends(get_api_key)):
    caddy_server.remove_custom_domain(domain)
    return "OK"

@domain_api.get("/domains/verify/{domain}")
async def verify_domain(domain: str, api_key:APIKey = Depends(get_api_key)):
    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    if os.path.exists(filepath):
        with open(filepath, "r") as file:
            content = file.read()
    else:
        content = generate_random_string()
        with open(filepath, "w") as file:
            file.write(content)
    async with httpx.AsyncClient() as client:
        response = await client.get(url=f"http://{domain}/well-known/bettercollected-challenge/{content}")

    if response.status_code == 200:
        return {"verified": True}
    else:
        return {"verified": False}
    
@domain_api.get("/well-known/bettercollected-challenge/{content}")
async def get_text_file(content: str, request: Request):
    domain = request.headers.get("host")
    filepath = os.path.join(texts_dir, f"{domain}.txt")
    stored_content = None
    if os.path.exists(filepath):
        with open(filepath, "r") as file:
            stored_content = file.read()
    if stored_content == content:
        return PlainTextResponse(content)
    else:
        raise HTTPException(status_code=404, detail="File not found")