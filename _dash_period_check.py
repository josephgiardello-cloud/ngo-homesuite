import re
import requests

base = 'http://127.0.0.1:5000'
s = requests.Session()
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
for p in ['30d','90d','ytd']:
    r = s.get(base + '/dashboard?period=' + p, timeout=10)
    print('PERIOD=' + p + ' STATUS=' + str(r.status_code) + ' HAS_LABEL=' + str(('Showing ' in r.text and 'focus.' in r.text)))
