import sys
import os

sys.path.append(os.getcwd())

try:
    from ngo_homesuite.app_factory import create_app
    # Try to find models and db
    import importlib
    
    # Try common locations
    models_to_try = [
        'core.models',
        'ngo_homesuite.models',
        'backend.models',
        'web.models'
    ]
    
    Donation = None
    User = None
    db = None
    
    for m in models_to_try:
        try:
            mod = importlib.import_module(m)
            Donation = getattr(mod, 'Donation', None)
            User = getattr(mod, 'User', None)
            if Donation and User:
                print(f"Loaded models from {m}")
                break
        except ImportError:
            continue
            
    db_to_try = [
        'core.database',
        'ngo_homesuite.extensions',
        'ngo_homesuite.app_factory',
        'ngo_homesuite'
    ]
    
    for d in db_to_try:
        try:
            mod = importlib.import_module(d)
            db = getattr(mod, 'db', None)
            if db:
                print(f"Loaded db from {d}")
                break
        except ImportError:
            continue

    if not all([Donation, User, db]):
         # If still missing, look for files
         print("Searching for model definitions...")
except Exception as e:
    print(f"Failure during setup: {e}")
    sys.exit(1)

app = create_app()
print(f"SQLALCHEMY_DATABASE_URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

with app.app_context():
    try:
        if Donation:
            count = Donation.query.count()
            print(f"Donation count: {count}")
        else:
            print("Donation model not found.")
    except Exception as e:
        print(f"Error counting donations: {e}")

client = app.test_client()

with app.app_context():
    if User:
        admin = User.query.filter_by(role='admin').first()
        if not admin:
            print("Admin user not found in database.")
        else:
            print(f"Logging in as admin: {admin.username}")
            with client.session_transaction() as sess:
                 sess['_user_id'] = str(admin.id)
                 sess['_fresh'] = True

            response = client.get('/donations', follow_redirects=True)
            print(f"Final status code: {response.status_code}")
            print(f"Redirect chain length: {len(response.history)}")
    else:
        print("User model not found.")
