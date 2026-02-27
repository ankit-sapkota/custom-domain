import os
import errno
import string
import random
import logging

import dns.resolver
import requests

logger = logging.getLogger(__name__)


def generate_random_string(length=32):
    letters = string.ascii_letters + string.digits
    return "bettercollected_" + ''.join(random.choice(letters) for i in range(length))


def silent_remove_file(filename):
    try:
        os.remove(filename)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


def get_server_ip() -> str | None:
    """Get this server's public IP address.

    Uses SERVER_IP env var if set, otherwise auto-detects via ipify.
    """
    server_ip = os.environ.get("SERVER_IP")
    if server_ip:
        return server_ip
    try:
        resp = requests.get("https://api.ipify.org", timeout=5)
        return resp.text.strip()
    except Exception as e:
        logger.error(f"Failed to detect server IP: {e}")
        return None


def _make_resolver() -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    return resolver


def check_a_record(domain: str, expected_ip: str) -> bool:
    """Return True if *domain* has an A record matching *expected_ip*."""
    try:
        answers = _make_resolver().resolve(domain, "A")
        return any(str(rdata) == expected_ip for rdata in answers)
    except Exception as e:
        logger.warning(f"A record check failed for {domain}: {e}")
        return False


def get_a_records(domain: str) -> list[str]:
    """Return all A-record IPs for *domain*."""
    try:
        answers = _make_resolver().resolve(domain, "A")
        return [str(rdata) for rdata in answers]
    except Exception:
        return []


def check_txt_record(domain: str, expected_value: str) -> bool:
    """Return True if *domain* has a TXT record equal to *expected_value*."""
    try:
        answers = _make_resolver().resolve(domain, "TXT")
        for rdata in answers:
            for txt_string in rdata.strings:
                if txt_string.decode() == expected_value:
                    return True
        return False
    except Exception as e:
        logger.warning(f"TXT record check failed for {domain}: {e}")
        return False