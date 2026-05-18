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
    has_export = 'Export Snapshot' in text
    has_filter_strip = 'dash-filter-strip' in text
    print(path + ' STATUS=' + str(r.status_code) + ' EXPORT=' + str(has_export) + ' FILTER_STRIP=' + str(has_filter_strip))
exp = s.get(base + '/dashboard/export?period=30d', timeout=10)
print('/dashboard/export?period=30d STATUS=' + str(exp.status_code) + ' JSON=' + str(exp.headers.get('content-type','').startswith('application/json')))
