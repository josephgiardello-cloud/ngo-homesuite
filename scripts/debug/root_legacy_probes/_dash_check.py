import re
import requests

base = "http://127.0.0.1:5000"
s = requests.Session()

login_page = s.get(base + "/auth/login", timeout=10)
print(f"LOGIN_GET_STATUS={login_page.status_code}")

m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page.text)
csrf = m.group(1) if m else ""
print(f"HAS_CSRF={bool(csrf)}")

payload = {
    "username": "admin",
    "password": "admin123!",
    "csrf_token": csrf,
}

login_resp = s.post(base + "/auth/login", data=payload, allow_redirects=True, timeout=15)
print(f"LOGIN_POST_FINAL_STATUS={login_resp.status_code}")
print(f"LOGIN_POST_FINAL_URL={login_resp.url}")
print(f"LOGIN_HAS_500={'Internal Server Error' in login_resp.text}")

dash = s.get(base + "/dashboard", timeout=15)
print(f"DASH_STATUS={dash.status_code}")
print(f"DASH_HAS_500={'Internal Server Error' in dash.text}")
print(f"DASH_HAS_HEADER={'<h1 style=\"margin-bottom:0.35rem;\">Dashboard</h1>' in dash.text}")
