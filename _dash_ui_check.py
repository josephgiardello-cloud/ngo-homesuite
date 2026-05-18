import re
import requests

s = requests.Session()
base = 'http://127.0.0.1:5000'
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
page = s.get(base + '/dashboard', timeout=10)
print('STATUS=' + str(page.status_code))
print('HAS_DASH_CLASS=' + str('dash-title' in page.text))
print('HAS_SHELL=' + str('dash-shell' in page.text))
