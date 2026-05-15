import requests
import os

def add_subscriber(email, first_name, last_name):
    api_key = os.getenv('MAILCHIMP_API_KEY')
    list_id = os.getenv('MAILCHIMP_LIST_ID')
    url = f'https://usX.api.mailchimp.com/3.0/lists/{list_id}/members'
    data = {
        'email_address': email,
        'status': 'subscribed',
        'merge_fields': {
            'FNAME': first_name,
            'LNAME': last_name
        }
    }
    resp = requests.post(url, auth=('anystring', api_key), json=data)
    return resp.status_code == 200
