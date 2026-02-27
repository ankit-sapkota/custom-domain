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
from app.domain_queue import pending_queue
from app.security import API_KEY_NAME, COOKIE_DOMAIN, get_api_key
from app.utils import check_a_record, check_txt_record

# Load all environment variables from .env uploaded_file
load_dotenv()
logger = logging.getLogger(__name__)

# Interval (in seconds) between domain-audit runs.  Default: 1 hour.
DOMAIN_AUDIT_INTERVAL = int(os.environ.get("DOMAIN_AUDIT_INTERVAL", 3600))

# Interval (in seconds) between pending-domain verification polls.  Default: 60 s.
PENDING_POLL_INTERVAL = int(os.environ.get("PENDING_POLL_INTERVAL", 60))


# Directory where per-domain TXT tokens are stored.
TEXTS_DIR = os.path.join(os.getcwd(), "domains", "texts")


async def _pending_verification_loop():
    """Every PENDING_POLL_INTERVAL seconds, check all pending domains.

    * If both A and TXT records are verified → promote into Caddy and
      remove from the pending queue.
    * Expired entries (>24 h by default) are marked as ``failed``.
    """
    while True:
        await asyncio.sleep(PENDING_POLL_INTERVAL)
        try:
            # 1. Mark expired entries as failed.
            failed = pending_queue.cleanup_expired()
            if failed:
                logger.info(f"Pending queue: domains marked failed (expired): {failed}")

            # 2. Try to verify remaining *pending* domains (skip failed).
            server_ip = caddy_server.server_ip
            if not server_ip:
                logger.warning("Server IP unknown — skipping pending verification.")
                continue

            pending = pending_queue.get_pending_only()
            for domain, info in pending.items():
                upstream = info.get("upstream", caddy_server.saas_upstream)

                # A record check
                a_ok = check_a_record(domain, server_ip)

                # TXT record check — read persisted token
                txt_ok = False
                txt_filepath = os.path.join(TEXTS_DIR, f"{domain}.txt")
                if os.path.exists(txt_filepath):
                    try:
                        with open(txt_filepath, "r") as fh:
                            txt_token = fh.read().strip()
                        if txt_token:
                            txt_ok = check_txt_record(domain, txt_token)
                    except OSError:
                        pass

                if a_ok and txt_ok:
                    logger.info(f"Pending '{domain}' fully verified (A+TXT) — promoting to Caddy.")
                    if caddy_server.promote_domain(domain, upstream):
                        pending_queue.remove(domain)
                    else:
                        logger.error(f"Failed to promote '{domain}' even though DNS verified.")
                else:
                    logger.debug(
                        f"Pending '{domain}': A={'OK' if a_ok else 'FAIL'}, "
                        f"TXT={'OK' if txt_ok else 'FAIL'} — not yet ready."
                    )
        except Exception as exc:
            logger.error(f"Pending verification error: {exc}")


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
    pending_task = asyncio.create_task(_pending_verification_loop())
    logger.info(f"Pending domain verification poll started (interval={PENDING_POLL_INTERVAL}s)")
    yield
    # --- shutdown ---
    audit_task.cancel()
    pending_task.cancel()
    for task in (audit_task, pending_task):
        try:
            await task
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
