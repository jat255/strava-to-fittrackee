from requests_oauthlib import OAuth2Session
from typing import Union
from dotenv import load_dotenv
from datetime import datetime, timedelta
import gpxpy
import json
import os
from pathlib import Path
from pprint import pprint
import logging

logger = logging.getLogger(__name__)
logging.basicConfig()

def get_or_raise_env(value: str, 
                     allow_none: bool = False) -> Union[str, None]:
  """
  Loads environment settings from a .env file in the current directory,
  and then checks the updated environment for the variable specified
  in ``value``. If the value is not found (and ``allow_none`` is
  ``False``), the method will raise an Exception.

  Parameters:
  -----------
  value:
    The environment variable to retrieve
  allow_none:
    Whether to allow missing values that will be returned as ``None``

  Returns:
  --------
  val:
    The environment variable value, if found, or ``None`` if not

  Raises:
  -------
  EnvironmentError:
    Raised if ``allow_none`` is False and the value is not found in the environment
  """
  load_dotenv()
  val = os.environ.get(value, None)
  if allow_none is False and val is None:
    raise EnvironmentError(f"No {value} defined in environment, "
                           f"please check the .env file (or README.md "
                           f"for more info)")
  return val

def load_conf(env_var):
  token_file = get_or_raise_env(env_var)
  token_path = Path(token_file)
  if token_path.exists():
    with open(token_path, 'r') as f:
      tokens = json.load(f)
  else:
    tokens = None
  return tokens

def save_conf(env_var, tokens):
  logger.debug(f'Saving tokens for {env_var}')
  token_file = get_or_raise_env(env_var)
  with open(Path(token_file), 'w') as f:
    json.dump(tokens, f, indent=2)

def is_token_expired(tokens):
  return datetime.now().timestamp() > tokens['expires_at']

class StravaConnector:

  def __init__(self):
    logger.debug("Initializing StravaConnector")
    self.tokens = load_conf('STRAVA_TOKEN_FILE')
    self.client_id = get_or_raise_env('STRAVA_CLIENT_ID')
    self.client_secret = get_or_raise_env('STRAVA_CLIENT_SECRET')
    self.authorize_url = 'https://www.strava.com/oauth/authorize'
    self.base_url = 'https://www.strava.com/api/v3'
    self.token_url = self.base_url + '/oauth/token'
    self.client = self.auth()
    
  def web_application_flow(self):
    logger.debug("Running Web Application Flow")
    redirect_uri = 'https://localhost'
    scope = ['activity:read_all']
    oauth = OAuth2Session(self.client_id, 
                          redirect_uri=redirect_uri,          
                          scope=scope)
    authorization_url, state = oauth.authorization_url(self.authorize_url)
    print(f'Please go to {authorization_url} and authorize access.')
    
    authorization_response = input('\nEnter the full callback URL from the browser address bar after you are redirected and press <enter>:\n\n')
    self.tokens = oauth.fetch_token(self.token_url,
      authorization_response=authorization_response,
      client_secret=self.client_secret, include_client_id=True)
  
    save_conf('STRAVA_TOKEN_FILE', self.tokens)
    return oauth

  def get_refreshing_client(self):
    refresh_params = {
        'client_id': self.client_id,
        'client_secret': self.client_secret,
    }
    client = OAuth2Session(self.client_id, 
      token=self.tokens, 
      auto_refresh_url=self.token_url,
      auto_refresh_kwargs=refresh_params, 
      token_updater=lambda x: save_conf('STRAVA_TOKEN_FILE', x))
    return client

  def auth(self):
    """
    Checks if a valid access token exists in the token file;
    if not, tries to get a new one via a refresh token (if present)
    or prompts the user to authenticate in order to get a brand new
    token. 
    """
    logger.debug("Setting up Strava auth")
    if self.tokens is None:
      logger.debug("No Strava tokens found; fetching new ones")
      return self.web_application_flow()
    else:
      logger.debug("Using existing Strava tokens with self-refreshing client")
      return self.get_refreshing_client()

  def get_activities(self, limit: Union[int, None] = 30):
    """
    If ``limit`` is ``None``, get all activities available (useful for initial 
    run, perhaps), otherwise get a limited number (default: 30) 
    """
    if limit is None:
      logger.debug('Getting all Strava activities since "limit" was "None"')
      page = 1
      all_activities = []
      while True:
        r = self.client.get(self.base_url + '/athlete/activities',
                             params={'per_page': 30, 'page': page})
        r.raise_for_status()
        if len(r.json()) == 0:
          logger.debug(f"No more activities found "
                       f"(total activities: {len(all_activities)})")
          return all_activities
        else:
          all_activities.extend(r.json())
          logger.debug(f'Fetched page {page} of activities '
                       f"(fetched {len(all_activities)} so far)")
          page += 1
    else:
      logger.debug(f'Getting last {limit} activities')
      r = self.client.get(self.base_url + '/athlete/activities',
                          params={'per_page': limit})
      r.raise_for_status()
      activities = r.json()
      return activities

  def create_activity_from_strava(self, activity: dict):
    activity_id = activity['id']
    
    logger.debug(f'Getting latitude and longitude for activity {activity_id}')
    r = self.client.get(self.base_url + f"/activities/{activity_id}/streams",
                        params={'keys': ['latlng']})
    r.raise_for_status()
    latlng = r.json()[0]['data']
    
    logger.debug(f'Getting timepoints for activity {activity_id}')
    r = self.client.get(self.base_url + f"/activities/{activity_id}/streams",
                        params={'keys': ['time']})
    r.raise_for_status()
    time_list = r.json()[1]['data']
    
    logger.debug(f'Getting altitude for activity {activity_id}')
    r = self.client.get(self.base_url + f"/activities/{activity_id}/streams",
                        params={'keys': ['altitude']})
    r.raise_for_status()
    altitude = r.json()[1]['data']

    logger.debug(f'Getting velocity for activity {activity_id}')
    r = self.client.get(self.base_url + f"/activities/{activity_id}/streams",
                        params={'keys': ['velocity_smooth']})
    r.raise_for_status()
    velocity = r.json()[0]['data']

    return Activity(activity_dict=activity, 
                    latlng=latlng, 
                    time_list=time_list, 
                    altitude=altitude, 
                    velocity=velocity)

class Activity:
  def __init__(self, activity_dict, latlng, time_list, altitude, velocity):
    self.title = activity_dict['name']
    self.activity_dict = activity_dict
    self.start_time = datetime.strptime(activity_dict['start_date'], '%Y-%m-%dT%H:%M:%SZ')
    self.lat = [i[0] for i in latlng]
    self.long = [i[1] for i in latlng]
    self.time = [(self.start_time + timedelta(seconds=t)) for t in time_list]
    self.altitude = altitude
    self.velocity = velocity
  
  def as_gpx(self):
    """
    Taken partially from https://stackoverflow.com/a/70665366
    """
    gpx = gpxpy.gpx.GPX()
    
    # Create first track in our GPX:
    gpx_track = gpxpy.gpx.GPXTrack(name=self.title)
    gpx.tracks.append(gpx_track)
    
    # Create first segment in our GPX track:
    gpx_segment = gpxpy.gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)
    
    # Create points:
    for time, lat, long, alt, vel in \
      zip(self.time, self.lat, self.long, self.altitude, self.velocity):
        gpx_segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, long, 
                                                          elevation=alt, 
                                                          time=time, speed=vel))
    
    return gpx

  def as_xml(self):
    return self.as_gpx().to_xml()


if __name__ == '__main__':
  logger.setLevel(logging.DEBUG)
  strava = StravaConnector()
  activities = strava.get_activities()
  pprint(activities[0])
  act = strava.create_activity_from_strava(activities[0])
  with open('test.gpx', 'w') as f:
    f.write(act.as_xml())

  pass
