import logging
import os

from dotenv import load_dotenv
from fastapi import HTTPException
import validators

from app.caddy.caddy_config import CaddyAPIConfigurator
from app.utils import check_a_record, get_server_ip
from app.domain_queue import pending_queue

HTTPS_PORT = 443

DEFAULT_ADMIN_URL = 'http://localhost:2019'
DEFAULT_CADDY_FILE = "domains/caddy.json"
DEFAULT_SAAS_UPSTREAM = "example.com:443"
DEFAULT_LOCAL_PORT = f"{HTTPS_PORT}"

load_dotenv()

logger = logging.getLogger(__name__)


class Caddy:

    def __init__(self):
        self.admin_url = os.environ.get('CADDY_ADMIN_URL', DEFAULT_ADMIN_URL)
        self.config_json_file = os.environ.get('CADDY_CONFIG_FILE', DEFAULT_CADDY_FILE)
        self.saas_upstream = os.environ.get('SAAS_UPSTREAM', DEFAULT_SAAS_UPSTREAM)
        self.local_port = os.environ.get('LOCAL_PORT', DEFAULT_LOCAL_PORT)
        self.disable_https = os.environ.get('DISABLE_HTTPS', 'False').upper() == "TRUE"
        self.server_ip = get_server_ip()

        if self.server_ip:
            logger.info(f"Server public IP: {self.server_ip}")
        else:
            logger.warning("Could not determine server public IP. A-record validation will fail.")

        self.configurator = CaddyAPIConfigurator(
            api_url=self.admin_url,
            https_port=self.local_port,
            disable_https=self.disable_https
        )

        if not self.configurator.load_config_from_file(self.config_json_file):
            self.configurator.init_config()

    def add_custom_domain(self, domain, upstream):
        """Validate the domain name and enqueue it for background verification.

        The domain is NOT added to Caddy immediately.  A background polling
        loop will promote it once DNS is verified.
        """
        if not validators.domain(domain):
            raise HTTPException(status_code=400, detail=f"{domain} is not a valid domain")

        upstream = upstream or self.saas_upstream

        # If already live in Caddy, nothing to do.
        if domain in self.list_domains():
            logger.info(f"Domain '{domain}' is already active in Caddy.")
            return

        # Add to pending queue — background loop will verify & promote.
        pending_queue.add(domain, upstream)

    def promote_domain(self, domain: str, upstream: str) -> bool:
        """Add a verified domain directly into the live Caddy config."""
        if not self.configurator.add_domain(domain, upstream):
            logger.error(f"Failed to promote domain '{domain}' to Caddy.")
            return False
        self.configurator.save_config(self.config_json_file)
        logger.info(f"Promoted '{domain}' into live Caddy config.")
        return True

    def remove_custom_domain(self, domain):
        if not validators.domain(domain):
            raise HTTPException(status_code=400, detail=f"{domain} is not a valid domain")

        if not self.configurator.delete_domain(domain):
            raise HTTPException(status_code=400, detail=f"Failed to remove domain: {domain}. Might not exist.")

        self.configurator.save_config(self.config_json_file)

    def deployed_config(self):
        return self.configurator.config

    def list_domains(self):
        return self.configurator.list_domains()

    def audit_domains(self):
        """Remove domains whose A record no longer points to this server."""
        if not self.server_ip:
            logger.warning("Server IP unknown — skipping domain audit.")
            return []

        domains = self.list_domains()
        removed: list[str] = []

        for domain in domains:
            if not check_a_record(domain, self.server_ip):
                logger.info(f"Audit: removing '{domain}' — A record no longer points to {self.server_ip}")
                try:
                    self.configurator.delete_domain(domain)
                    removed.append(domain)
                except Exception as e:
                    logger.error(f"Audit: failed to remove '{domain}': {e}")

        if removed:
            self.configurator.save_config(self.config_json_file)
            logger.info(f"Audit complete. Removed {len(removed)} domain(s): {removed}")
        else:
            logger.info("Audit complete. All domains are correctly pointed.")

        return removed


caddy_server = Caddy()
