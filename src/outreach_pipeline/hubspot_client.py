"""HubSpot API client with rate limiting and safety checks.

Provides both sync (HubSpotClient) and async (AsyncHubSpotClient) interfaces.
Sync is used by CLI scripts, async by the LangGraph agent graph.
"""
import asyncio
import time
import random
import requests
from datetime import datetime
import pytz

import httpx

# API endpoints
HUBSPOT_API_BASE = "https://api.hubapi.com"
CONTACTS_SEARCH_URL = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/search"
PROPERTIES_URL = f"{HUBSPOT_API_BASE}/crm/v3/properties/contacts"
MARKETING_EMAILS_URL = f"{HUBSPOT_API_BASE}/marketing/v3/emails"

# PII fields to guard against
PII_FIELDS = {"email", "firstname", "lastname", "phone", "mobilephone", "address", "city", "zip", "state"}

class HubSpotError(Exception):
    """HubSpot API error."""
    pass

class PIIError(Exception):
    """Raised when PII is detected in response."""
    pass

class TokenBucket:
    """Simple token bucket for rate limiting."""
    def __init__(self, rate_per_10s=90):
        self.rate = rate_per_10s
        self.tokens = rate_per_10s
        self.last_update = time.time()
    
    def acquire(self):
        """Wait if necessary, then consume a token."""
        now = time.time()
        elapsed = now - self.last_update
        self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / 10.0))
        self.last_update = now
        
        if self.tokens < 1:
            sleep_time = (1 - self.tokens) * (10.0 / self.rate)
            time.sleep(sleep_time)
            self.tokens = 0
        else:
            self.tokens -= 1


class HubSpotClient:
    """HubSpot API client with rate limiting and PII safety."""
    
    def __init__(self, token, config):
        self.token = token
        self.config = config
        self.safety_mode = config.get("safety_mode", True)
        self.bucket = TokenBucket(config.get("rate_limit_per_10s", 90))
        self.tz = pytz.timezone(config.get("report_timezone", "America/New_York"))
    
    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
    
    def request_with_retry(self, method, url, json=None, max_retries=3):
        """Make request with retry on 429/5xx."""
        self.bucket.acquire()
        
        for attempt in range(max_retries):
            try:
                resp = requests.request(method, url, headers=self._headers(), json=json, timeout=30)
                
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 10))
                    jitter = random.uniform(0, 2)
                    time.sleep(retry_after + jitter)
                    continue
                
                if resp.status_code >= 500:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    time.sleep(backoff)
                    continue
                
                if resp.status_code == 401:
                    raise HubSpotError("401 Unauthorized - check your HUBSPOT_TOKEN")
                if resp.status_code == 403:
                    raise HubSpotError("403 Forbidden - check Private App scopes")
                
                resp.raise_for_status()
                return resp.json()
                
            except requests.exceptions.Timeout:
                if attempt == max_retries - 1:
                    raise HubSpotError("Request timed out after retries")
                time.sleep(2 ** attempt)
        
        raise HubSpotError(f"Request failed after {max_retries} retries")
    
    def pii_guard(self, response):
        """Check response for PII fields. Raises PIIError if found."""
        results = response.get("results", [])
        for result in results:
            props = result.get("properties", {})
            found_pii = PII_FIELDS & set(props.keys())
            if found_pii:
                raise PIIError(f"PII fields detected in response: {found_pii}")
    
    def safety_check(self, response):
        """In safety mode, verify response only contains expected fields."""
        if not self.safety_mode:
            return
        
        results = response.get("results", [])
        for result in results:
            props = result.get("properties", {})
            # Allow hs_object_id plus system timestamps (non-PII)
            allowed = {"hs_object_id", "createdate", "lastmodifieddate"}
            unexpected = set(props.keys()) - allowed
            if unexpected:
                raise PIIError(f"Unexpected properties in response: {unexpected}. "
                              f"Use --allow-results to bypass (not recommended).")
    
    def get_count(self, filters):
        """Get count of contacts matching filters. Returns only total, discards results."""
        payload = {
            "filterGroups": [{"filters": filters}] if filters else [],
            "properties": ["hs_object_id"],  # Minimal property to avoid PII
            "limit": 1  # We only need the total
        }
        
        response = self.request_with_retry("POST", CONTACTS_SEARCH_URL, json=payload)
        
        # Safety checks
        self.pii_guard(response)
        self.safety_check(response)
        
        # Return only the total count
        return response.get("total", 0)
    
    def build_date_filter(self, property_name, year, month):
        """Build date range filter for a month."""
        start = self.tz.localize(datetime(year, month, 1))
        
        # Calculate end of month
        if month == 12:
            end = self.tz.localize(datetime(year + 1, 1, 1))
        else:
            end = self.tz.localize(datetime(year, month + 1, 1))
        
        # HubSpot uses milliseconds since epoch
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000) - 1  # End of last day
        
        return {
            "propertyName": property_name,
            "operator": "BETWEEN",
            "value": str(start_ms),
            "highValue": str(end_ms)
        }
    
    def build_bucket_filter(self, property_name, value):
        """Build filter for a property value. Supports single value or list (IN operator)."""
        if value is None:
            return {
                "propertyName": property_name,
                "operator": "NOT_HAS_PROPERTY"
            }
        # Support list of values using IN operator
        if isinstance(value, list):
            return {
                "propertyName": property_name,
                "operator": "IN",
                "values": value
            }
        return {
            "propertyName": property_name,
            "operator": "EQ",
            "value": value
        }
    
    def test_connection(self):
        """Test API connection and return portal info."""
        url = f"{HUBSPOT_API_BASE}/account-info/v3/details"
        try:
            response = self.request_with_retry("GET", url)
            return {
                "success": True,
                "portal_id": response.get("portalId"),
                "hub_domain": response.get("uiDomain")
            }
        except HubSpotError as e:
            return {"success": False, "error": str(e)}
    
    def get_property(self, property_name):
        """Get property definition including options for enumeration types."""
        url = f"{PROPERTIES_URL}/{property_name}"
        return self.request_with_retry("GET", url)
    
    def get_all_properties(self):
        """Get all contact properties."""
        return self.request_with_retry("GET", PROPERTIES_URL)
    
    def search_contacts_raw(self, filters, properties=None):
        """Search contacts with custom properties returned (for testing)."""
        payload = {
            "filterGroups": [{"filters": filters}] if filters else [],
            "properties": properties or ["hs_object_id"],
            "limit": 10
        }
        return self.request_with_retry("POST", CONTACTS_SEARCH_URL, json=payload)
    
    def get_marketing_emails(self, limit=100):
        """Fetch all marketing emails with statistics."""
        emails = []
        after = None
        
        while True:
            params = {"limit": min(limit, 100)}
            if after:
                params["after"] = after
            
            url = f"{MARKETING_EMAILS_URL}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
            response = self.request_with_retry("GET", url)
            
            results = response.get("results", [])
            emails.extend(results)
            
            # Check for pagination
            paging = response.get("paging", {})
            next_page = paging.get("next", {})
            after = next_page.get("after")
            
            if not after or len(emails) >= limit:
                break
        
        return emails
    
    def get_email_stats(self, email_id):
        """Get statistics for a specific email."""
        url = f"{MARKETING_EMAILS_URL}/{email_id}"
        return self.request_with_retry("GET", url)

    def get_contact_emails(self, contact_id):
        """Fetch email engagements associated with a contact (read-only).

        Returns list of dicts with subject, body_preview, direction, timestamp.
        direction='INCOMING' indicates a reply from the contact.
        """
        assoc_url = (
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/"
            f"{contact_id}/associations/emails"
        )
        assoc_resp = self.request_with_retry("GET", assoc_url)
        results = assoc_resp.get("results", [])
        if not results:
            return []

        email_ids = [r["id"] for r in results]
        emails = []
        for eid in email_ids:
            url = f"{HUBSPOT_API_BASE}/crm/v3/objects/emails/{eid}"
            params = "properties=hs_email_subject,hs_email_text,hs_email_direction,hs_timestamp"
            data = self.request_with_retry("GET", f"{url}?{params}")
            props = data.get("properties", {})
            emails.append({
                "id": eid,
                "subject": props.get("hs_email_subject", ""),
                "body_preview": props.get("hs_email_text", ""),
                "direction": props.get("hs_email_direction", ""),
                "timestamp": props.get("hs_timestamp", ""),
            })
        return emails


# ---------------------------------------------------------------------------
# Async client — mirrors the sync API using httpx.AsyncClient
# ---------------------------------------------------------------------------

class AsyncTokenBucket:
    """Token bucket with async sleep for rate limiting.

    Sleep happens OUTSIDE the lock so concurrent coroutines aren't
    serialized behind one sleeping coroutine.
    """

    def __init__(self, rate_per_10s: int = 90):
        self.rate = rate_per_10s
        self.tokens = float(rate_per_10s)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        sleep_time = 0.0
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.rate, self.tokens + elapsed * (self.rate / 10.0))
            self.last_update = now

            if self.tokens < 1:
                sleep_time = (1 - self.tokens) * (10.0 / self.rate)
                self.tokens = 0
            else:
                self.tokens -= 1

        if sleep_time > 0:
            await asyncio.sleep(sleep_time)


# ---------------------------------------------------------------------------
# Shared singleton — one client, one rate limiter, one httpx pool
# ---------------------------------------------------------------------------

_async_client: "AsyncHubSpotClient | None" = None


def get_async_hubspot() -> "AsyncHubSpotClient":
    """Get or create the shared async HubSpot client singleton.

    When OUTREACH_MOCK_HUBSPOT=1 is set, returns an in-memory stub
    that serves fixture MQL contacts — used by the local staging
    stack so `ingest_contacts` works without a real HubSpot token.
    """
    global _async_client
    if _async_client is None:
        import os
        if os.environ.get("OUTREACH_MOCK_HUBSPOT") == "1":
            from outreach_pipeline.adapters.mock_hubspot import MockAsyncHubSpotClient
            _async_client = MockAsyncHubSpotClient()  # type: ignore[assignment]
        else:
            from outreach_pipeline.config import load_config
            config = load_config()
            _async_client = AsyncHubSpotClient(config["hubspot_token"], config)
    return _async_client


class AsyncHubSpotClient:
    """Async HubSpot API client. Uses httpx + asyncio.sleep for non-blocking I/O.

    Use ``get_async_hubspot()`` for the shared singleton (shared rate limiter
    and HTTP connection pool). Only call the constructor directly in tests.
    """

    def __init__(self, token: str, config: dict):
        self.token = token
        self.config = config
        self.safety_mode = config.get("safety_mode", True)
        self.bucket = AsyncTokenBucket(config.get("rate_limit_per_10s", 90))
        self.tz = pytz.timezone(config.get("report_timezone", "America/New_York"))
        self._http = httpx.AsyncClient(
            timeout=30,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )

    @classmethod
    def from_config(cls, config: dict | None = None) -> "AsyncHubSpotClient":
        """Create a NEW instance (own rate limiter). Prefer get_async_hubspot()."""
        if config is None:
            from outreach_pipeline.config import load_config
            config = load_config()
        return cls(config["hubspot_token"], config)

    async def aclose(self):
        """Close the underlying httpx client."""
        await self._http.aclose()

    async def arequest(self, method: str, url: str, *, json=None, max_retries: int = 3) -> dict:
        """Make an async request with retry on 429/5xx."""
        await self.bucket.acquire()

        for attempt in range(max_retries):
            try:
                resp = await self._http.request(method, url, json=json)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "10"))
                    jitter = random.uniform(0, 2)
                    await asyncio.sleep(retry_after + jitter)
                    continue

                if resp.status_code >= 500:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue

                if resp.status_code == 401:
                    raise HubSpotError("401 Unauthorized - check your HUBSPOT_TOKEN")
                if resp.status_code == 403:
                    raise HubSpotError("403 Forbidden - check Private App scopes")

                resp.raise_for_status()
                return resp.json()

            except httpx.TimeoutException:
                if attempt == max_retries - 1:
                    raise HubSpotError("Request timed out after retries")
                await asyncio.sleep(2 ** attempt)

        raise HubSpotError(f"Request failed after {max_retries} retries")

    async def aget_contact(self, contact_id: str, properties: list[str] | None = None) -> dict:
        """Fetch a single contact by ID with optional properties."""
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/{contact_id}"
        if properties:
            qs = ",".join(properties)
            url = f"{url}?properties={qs}"
        return await self.arequest("GET", url)

    async def asearch_contacts(self, filter_groups: list[dict], properties: list[str], limit: int = 100) -> list[dict]:
        """Search contacts with pagination. Returns all matching contacts.

        filter_groups: list of {"filters": [...]} dicts (OR between groups, AND within).
        """
        contacts = []
        after = None

        while True:
            payload: dict = {
                "filterGroups": filter_groups,
                "properties": properties,
                "limit": min(limit, 100),
            }
            if after:
                payload["after"] = after

            response = await self.arequest("POST", CONTACTS_SEARCH_URL, json=payload)
            contacts.extend(response.get("results", []))

            after = response.get("paging", {}).get("next", {}).get("after")
            if not after or len(contacts) >= limit:
                break

        return contacts

    async def aget_contact_profile(self, contact_id: str) -> dict:
        """Fetch full contact profile (v1 API — includes form submissions)."""
        url = f"{HUBSPOT_API_BASE}/contacts/v1/contact/vid/{contact_id}/profile"
        return await self.arequest("GET", url)

    async def aget_contact_emails(self, contact_id: str) -> list[dict]:
        """Fetch email engagements for a contact."""
        assoc_url = (
            f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/"
            f"{contact_id}/associations/emails"
        )
        assoc_resp = await self.arequest("GET", assoc_url)
        results = assoc_resp.get("results", [])
        if not results:
            return []

        emails = []
        for r in results:
            eid = r["id"]
            url = f"{HUBSPOT_API_BASE}/crm/v3/objects/emails/{eid}"
            qs = "properties=hs_email_subject,hs_email_text,hs_email_direction,hs_timestamp"
            data = await self.arequest("GET", f"{url}?{qs}")
            props = data.get("properties", {})
            emails.append({
                "id": eid,
                "subject": props.get("hs_email_subject", ""),
                "body_preview": props.get("hs_email_text", ""),
                "direction": props.get("hs_email_direction", ""),
                "timestamp": props.get("hs_timestamp", ""),
            })
        return emails
