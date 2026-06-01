import re
import requests

base = 'http://127.0.0.1:5000'
s = requests.Session()
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
r = s.get(base + '/dashboard?period=30d', timeout=10)
text = r.text
checks = {
  'focus_panel': 'Focus View: Choose what stays on screen' in text,
  'focus_toggle_kpis': 'data-focus-toggle="kpis"' in text,
  'sidebar_group_toggle': 'data-sidebar-group-toggle="fundraising"' in text,
  'cohort_trend': 'Donor Cohort Trend' in text,
}
print('/dashboard?period=30d STATUS=' + str(r.status_code))
for k,v in checks.items():
    print(k + '=' + str(v))
