import socket
import threading
import time
import urllib.request

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.app.api import create_app
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.browser


@pytest.fixture
def live_app(system):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        create_app(system.store, secure_cookies=False), host="127.0.0.1", port=port, log_level="warning",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if server.started:
                with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
                    assert response.status == 200
                break
            time.sleep(.05)
        else:
            pytest.fail("Browser test server did not become responsive")
        yield url
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def login(page, url, username="admin", tenant="alpha"):
    page.goto(url)
    page.get_by_label("Organization", exact=True).fill(tenant)
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Password", exact=True).fill(TEST_PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.locator("#identity")).to_contain_text(f"{username} (")
    expect(page.locator("#message")).to_have_text("Signed in.")


def test_staff_donation_allocations_and_audit(browser, live_app, system):
    context = browser.new_context()
    page = context.new_page()
    login(page, live_app, "fundraiser")
    page.get_by_label("Donor name", exact=True).fill("Browser donor")
    page.get_by_role("button", name="Add donor", exact=True).click()
    expect(page.locator("#message")).to_have_text("donor saved.")
    page.get_by_label("Donation amount", exact=True).fill("100.00")
    page.get_by_label("Purpose", exact=True).fill("Browser receipt")
    page.get_by_label("Receipt / idempotency key", exact=True).fill("browser-receipt")
    page.get_by_role("button", name="Record donation", exact=True).click()
    expect(page.locator("#message")).to_have_text("donation saved.")
    page.get_by_role("button", name="Record donation", exact=True).click()
    expect(page.locator("#message")).to_have_text("donation saved.")
    expect(page.locator("#donations tbody tr")).to_have_count(1)
    donation_id = system.services.list_entities(system.admin, "donations")[0]["id"]
    page.get_by_label("Donation ID", exact=True).fill(str(donation_id))
    page.get_by_label("Allocation amount", exact=True).fill("100.01")
    page.get_by_role("button", name="Save allocation", exact=True).click()
    expect(page.locator("#message")).to_have_text("Allocations exceed donation amount")
    page.get_by_label("Allocation amount", exact=True).fill("100")
    page.get_by_role("button", name="Save allocation", exact=True).click()
    expect(page.locator("#message")).to_have_text("allocation saved.")
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.locator("#message")).to_have_text("Signed out.")
    login(page, live_app)
    expect(page.locator("#audit")).to_contain_text("donation.create")
    expect(page.locator("#audit")).to_contain_text("donation.allocate.multi")
    context.close()


def test_two_admin_grants_permission_and_tenant_journey(browser, live_app, system):
    context = browser.new_context()
    page = context.new_page()
    login(page, live_app)
    page.get_by_label("Project name", exact=True).fill("Browser project")
    page.get_by_label("Project budget", exact=True).fill("100")
    page.get_by_role("button", name="Add project", exact=True).click()
    expect(page.locator("#message")).to_have_text("project saved.")
    page.get_by_label("Grant name", exact=True).fill("Browser grant")
    page.get_by_label("Grant amount", exact=True).fill("70")
    page.get_by_role("button", name="Create grant draft", exact=True).click()
    expect(page.locator("#message")).to_have_text("grant saved.")
    grant_id = system.services.list_entities(system.admin, "grants")[0]["id"]
    page.get_by_role("button", name=f"approve grant {grant_id}", exact=True).click()
    expect(page.locator("#grants")).to_contain_text("approved")
    expect(page.get_by_role("button", name=f"pay grant {grant_id}", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.locator("#message")).to_have_text("Signed out.")
    login(page, live_app, "payer")
    page.get_by_role("button", name=f"pay grant {grant_id}", exact=True).click()
    expect(page.locator("#grants")).to_contain_text("paid")
    expect(page.locator("#projects")).to_contain_text("70")
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.locator("#message")).to_have_text("Signed out.")
    login(page, live_app, "viewer")
    expect(page.get_by_role("button", name="Add donor", exact=True)).not_to_be_visible()
    expect(page.locator("#grants")).to_contain_text("Browser grant")
    page.get_by_role("button", name="Sign out", exact=True).click()
    expect(page.locator("#message")).to_have_text("Signed out.")
    login(page, live_app, tenant="beta")
    expect(page.locator("#grants tbody tr")).to_have_count(0)
    expect(page.locator("#projects tbody tr")).to_have_count(0)
    context.close()
