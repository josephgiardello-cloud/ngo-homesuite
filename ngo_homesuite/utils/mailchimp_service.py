import logging
import requests

from ngo_homesuite.config import get_runtime_settings

logger = logging.getLogger(__name__)


def _mailchimp_api_root() -> str:
    """Derive the Mailchimp API root from the API key datacenter suffix."""
    settings = get_runtime_settings()
    api_key = settings.mailchimp_api_key or ""
    dc = api_key.split("-")[-1] if "-" in api_key else "us1"
    return f"https://{dc}.api.mailchimp.com/3.0"


def add_subscriber(email, first_name, last_name):
    settings = get_runtime_settings()
    api_key = settings.mailchimp_api_key
    list_id = settings.mailchimp_list_id
    if not api_key or not list_id:
        logger.warning("Mailchimp not configured; subscriber not added for %s", email)
        return False
    url = f"{_mailchimp_api_root()}/lists/{list_id}/members"
    data = {
        'email_address': email,
        'status': 'subscribed',
        'merge_fields': {
            'FNAME': first_name,
            'LNAME': last_name
        }
    }
    resp = requests.post(url, auth=('anystring', api_key), json=data, timeout=10)
    if resp.status_code not in (200, 201):
        logger.warning("Mailchimp add_subscriber failed: %s %s", resp.status_code, resp.text[:200])
    return resp.status_code in (200, 201)


def remove_subscriber(email: str) -> bool:
    """Unsubscribe (archive) a member from the configured list."""
    import hashlib
    settings = get_runtime_settings()
    api_key = settings.mailchimp_api_key
    list_id = settings.mailchimp_list_id
    if not api_key or not list_id:
        logger.warning("Mailchimp not configured; cannot remove %s", email)
        return False
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    url = f"{_mailchimp_api_root()}/lists/{list_id}/members/{subscriber_hash}"
    resp = requests.patch(
        url, auth=("anystring", api_key),
        json={"status": "unsubscribed"}, timeout=10
    )
    return resp.status_code == 200


def update_subscriber_tags(email: str, tags: list[str], *, remove_tags: list[str] | None = None) -> bool:
    """Add/remove tags on a Mailchimp subscriber."""
    import hashlib
    settings = get_runtime_settings()
    api_key = settings.mailchimp_api_key
    list_id = settings.mailchimp_list_id
    if not api_key or not list_id:
        logger.warning("Mailchimp not configured; cannot update tags for %s", email)
        return False
    subscriber_hash = hashlib.md5(email.lower().encode()).hexdigest()
    url = f"{_mailchimp_api_root()}/lists/{list_id}/members/{subscriber_hash}/tags"
    payload_tags = [{"name": t, "status": "active"} for t in (tags or [])]
    payload_tags += [{"name": t, "status": "inactive"} for t in (remove_tags or [])]
    resp = requests.post(
        url, auth=("anystring", api_key),
        json={"tags": payload_tags}, timeout=10
    )
    return resp.status_code == 204


def sync_beneficiary_list(beneficiaries: list[dict]) -> dict:
    """Bulk sync a list of beneficiary dicts to Mailchimp.

    Each dict must have ``email``, ``first_name``, ``last_name``.
    Returns {"synced": N, "failed": N, "skipped": N}.
    """
    synced = failed = skipped = 0
    for b in beneficiaries:
        email = (b.get("email") or "").strip()
        if not email:
            skipped += 1
            continue
        ok = add_subscriber(email, b.get("first_name", ""), b.get("last_name", ""))
        if ok:
            synced += 1
        else:
            failed += 1
    return {"synced": synced, "failed": failed, "skipped": skipped}

