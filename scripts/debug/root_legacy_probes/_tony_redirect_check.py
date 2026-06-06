import re
import requests

base = 'http://127.0.0.1:5000'
s = requests.Session()
login = s.get(base + '/auth/login', timeout=10)
csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login.text).group(1)
s.post(base + '/auth/login', data={'username':'admin','password':'admin123!','csrf_token':csrf}, timeout=10)
r = s.get(base + '/tony-scoring', allow_redirects=False, timeout=10)
print('/tony-scoring STATUS=' + str(r.status_code) + ' LOCATION=' + str(r.headers.get('location')))
if r.status_code in (301,302,303,307,308):
    r2 = s.get(base + r.headers['location'], timeout=10)
    print('FOLLOW STATUS=' + str(r2.status_code) + ' HAS_TONY=' + str('TONY' in r2.text))
