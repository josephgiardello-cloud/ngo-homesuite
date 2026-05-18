from ngo_homesuite.app_factory import create_app
from ngo_homesuite.models.core import Donation

app = create_app()
print('DB_URI=', app.config.get('SQLALCHEMY_DATABASE_URI'))
with app.app_context():
    print('DONATION_COUNT=', Donation.query.count())

client = app.test_client()
login = client.post('/auth/login', data={'username':'admin','password':'admin123!'}, follow_redirects=True)
print('LOGIN_STATUS=', login.status_code)
resp = client.get('/donations', follow_redirects=True)
print('DONATIONS_STATUS=', resp.status_code)
print('FINAL_PATH=', resp.request.path if getattr(resp, 'request', None) else 'n/a')
print('BODY_HEAD=', resp.get_data(as_text=True)[:180].replace('\n',' '))
