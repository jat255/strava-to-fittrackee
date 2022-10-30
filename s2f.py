from requests_oauthlib import OAuth2Session
from dotenv import load_dotenv
import json
import os

load_dotenv()

def strava_auth():
  client_id = os.environ.get('STRAVA_CLIENT_ID')
  client_secret = os.environ.get('STRAVA_CLIENT_SECRET')
  redirect_uri = 'https://localhost'

  scope = ['activity:read_all']
  oauth = OAuth2Session(client_id, redirect_uri=redirect_uri, scope=scope)
  authorization_url, state = oauth.authorization_url(
        'https://www.strava.com/oauth/authorize')
  print(f'Please go to {authorization_url} and authorize access.')
  
  authorization_response = input('Enter the full callback URL from the browser:\n')
  token = oauth.fetch_token(
        'https://www.strava.com/api/v3/oauth/token',
        authorization_response=authorization_response,
        client_secret=client_secret, include_client_id=True)
  if token:
    with open('tokens.json', 'w') as f:
      json.dump(token, f, indent=2)

if __name__ == '__main__':
  strava_auth()
