import asyncio
import logging
import os

from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.models import APIKey
from fastapi.openapi.utils import get_openapi
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import RedirectResponse, JSONResponse

from app.api import domain_api
from app.caddy.caddy import caddy_server
from app.security import API_KEY_NAME, COOKIE_DOMAIN, get_api_key

# Load all environment variables from .env uploaded_file
load_dotenv()
logger = logging.getLogger(__name__)

# Interval (in seconds) between domain-audit runs.  Default: 1 hour.
DOMAIN_AUDIT_INTERVAL = int(os.environ.get("DOMAIN_AUDIT_INTERVAL", 3600))


async def _domain_audit_loop():
    """Periodically verify that every configured domain still has a valid
    A record pointing to this server.  Domains that fail the check are
    removed from the Caddy configuration."""
    while True:
        await asyncio.sleep(DOMAIN_AUDIT_INTERVAL)
        try:
            removed = caddy_server.audit_domains()
            if removed:
                logger.info(f"Scheduler removed stale domains: {removed}")
        except Exception as exc:
            logger.error(f"Domain audit error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    logger.info("App started")
    audit_task = asyncio.create_task(_domain_audit_loop())
    logger.info(f"Domain audit scheduler started (interval={DOMAIN_AUDIT_INTERVAL}s)")
    yield
    # --- shutdown ---
    audit_task.cancel()
    try:
        await audit_task
    except asyncio.CancelledError:
        pass
    logger.info("App is shutting down")


app = FastAPI(lifespan=lifespan)
app.include_router(domain_api)

# CORS support
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*')
ALLOWED_METHODS = os.environ.get('ALLOWED_METHODS', '*')
ALLOWED_HEADERS = os.environ.get('ALLOWED_HEADERS', '*')

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=ALLOWED_METHODS,
    allow_headers=ALLOWED_HEADERS,
)

# Trusted Hosts
trusted_hosts = os.environ.get('TRUSTED_HOSTS', None)
if trusted_hosts:
    trusted_hosts = [host.strip() for host in trusted_hosts.split(",")]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


@app.get("/logout", tags=["default"])
async def logout_and_remove_cookie():
    response = RedirectResponse(url="/")
    response.delete_cookie(API_KEY_NAME, domain=COOKIE_DOMAIN)
    return response


@app.get("/openapi.json", tags=["default"])
async def get_open_api_endpoint(api_key: APIKey = Depends(get_api_key)):
    response = JSONResponse(
        get_openapi(title="SaaS HTTPS API", version='1.0.0', routes=app.routes)
    )
    return response


@app.get("/docs", tags=["default"])
async def get_documentation(api_key: APIKey = Depends(get_api_key)):
    response = get_swagger_ui_html(openapi_url="/openapi.json", title="docs")
    response.set_cookie(
        API_KEY_NAME,
        value=api_key,
        domain=COOKIE_DOMAIN,
        httponly=True,
        max_age=1800,
        expires=1800,
    )
    return response
