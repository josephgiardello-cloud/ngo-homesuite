import re
import requests

base = 'http://127.0.0.1:5000'
s = requests.Session()
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
checks = [
    '/dashboard?period=30d',
    '/dashboard?period=90d',
    '/dashboard?period=ytd',
    '/dashboard?period=custom&start_date=2026-01-01&end_date=2026-03-31',
]
for path in checks:
    r = s.get(base + path, timeout=10)
    text = r.text
    print(path + ' STATUS=' + str(r.status_code) + ' HAS_CUSTOM_FORM=' + str('name="start_date"' in text and 'name="end_date"' in text) + ' HAS_PERIOD_HINT=' + str('Showing ' in text and 'focus.' in text))
