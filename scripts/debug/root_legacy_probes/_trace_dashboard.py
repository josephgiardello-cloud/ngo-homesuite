import traceback
from ngo_homesuite.app_factory import create_app
from ngo_homesuite.models.core import User

app = create_app()
app.config['TESTING'] = True
app.config['PROPAGATE_EXCEPTIONS'] = True

with app.app_context():
    admin = User.query.filter_by(username='admin').first()
    print('ADMIN_ID=' + str(admin.id if admin else None))

client = app.test_client()
with client.session_transaction() as sess:
    if admin is not None:
        sess['_user_id'] = str(admin.id)
        sess['_fresh'] = True

try:
    resp = client.get('/dashboard')
    print('STATUS=' + str(resp.status_code))
    print('HAS_500=' + str('Internal Server Error' in resp.get_data(as_text=True)))
except Exception:
    traceback.print_exc()
