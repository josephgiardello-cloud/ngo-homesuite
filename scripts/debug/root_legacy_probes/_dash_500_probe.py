import re
import requests

base = 'http://127.0.0.1:5000'
s = requests.Session()
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
r = s.get(base + '/dashboard?period=30d', timeout=10)
print('STATUS=' + str(r.status_code))
print(r.text[:1400])
