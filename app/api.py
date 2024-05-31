import os
from typing import Optional
import httpx
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.openapi.models import APIKey
from fastapi.responses import PlainTextResponse
import dns.resolver
from app.caddy.caddy import caddy_server
from app.security import get_api_key
from app.utils import generate_random_string, silent_remove_file
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
    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    silent_remove_file(filepath)
    return "OK"

@domain_api.get("/domains/verify/{domain}")
async def verify_domain(domain: str, api_key:APIKey = Depends(get_api_key)):
    filename = f"{domain}.txt"
    filepath = os.path.join(texts_dir, filename)
    if os.path.exists(filepath):
        async with aiofiles.open(filepath,mode= "r") as file:
            content = await file.read()
    else:
        content = generate_random_string()
        async with aiofiles.open(filepath,mode= "w") as file:
            await file.write(content)
    response = None
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url=f"http://{domain}:9000/.well-known/acme-challenge/{content}")
        except Exception as e:
            print(e)
            pass

    resp = {
        "records":[
            {
                "name": "Domain",
                "type": "A",
                "value": ""
            },
            {
                "name": "Domain",
                "type": "TXT",
                "value": content
            }
        ]
    }

    resp["domain_verified"] = True if response and response.status_code == 200 else False
    resp["txt_verified"] = True if await verify_txt_record_of_domain(domain=domain, txt_record=content) else False
    return resp
    
@domain_api.get("/.well-known/acme-challenge/{content}")
async def get_text_file(content: str, request: Request):
    domain = request.url.hostname
    print(domain)
    filepath = os.path.join(texts_dir, f"{domain}.txt")
    stored_content = None
    if os.path.exists(filepath):
        async with aiofiles.open(filepath, "r") as file:
            stored_content = await file.read()
    if stored_content == content:
        return PlainTextResponse(content)
    else:
        raise HTTPException(status_code=404, detail="Not found")
    
async def verify_txt_record_of_domain(domain:str, txt_record:str):
    try:
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ['1.1.1.1']
        answers = resolver.resolve(domain, 'TXT')

        for rdata in answers:
            for txt_string in rdata.strings:
                if txt_string.decode() == txt_record:
                    return True
        return False
    except dns.resolver.NoAnswer:
        print(f"No TXT record found for {domain}")
    except dns.resolver.NXDOMAIN:
        print(f"The domain {domain} does not exist")
    except Exception as e:
        print(f"An error occurred: {e}")
