from requests_oauthlib import OAuth2Session
from typing import Union
from dotenv import load_dotenv
from datetime import datetime, timedelta
import gpxpy
import json
import os
from pathlib import Path
import tempfile
import atexit
from pprint import pprint
import logging
import urllib3

logger = logging.getLogger(__name__)
logging.basicConfig()
logger.setLevel(logging.DEBUG)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# create directory for storing files temporarily and delete it
# when the program finishes (using atexit module)
tempdir = tempfile.TemporaryDirectory()
logger.debug(f"tempdir is {tempdir}")
atexit.register(lambda: logger.debug(f"Removing {tempdir}") and tempdir.cleanup())

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

  def create_activity_from_strava(self, activity: dict, get_streams: bool = True):
    activity_id = activity['id']
    
    if get_streams:
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
    else:
      latlng = [(None, None)]
      time_list = [0]
      altitude = [None]
      velocity = [None]

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
    gpx_track = gpxpy.gpx.GPXTrack(
      name=self.title,
      description=self.activity_dict['type'])
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


class FitTrackeeConnector:

  def __init__(self):
    logger.debug("Initializing FitTrackeeConnector")
    self.tokens = load_conf('FITTRACKEE_TOKEN_FILE')
    self.host = get_or_raise_env('FITTRACKEE_HOST')
    self.client_id = get_or_raise_env('FITTRACKEE_CLIENT_ID')
    self.client_secret = get_or_raise_env('FITTRACKEE_CLIENT_SECRET')
    self.authorize_url = f'https://{self.host}/profile/apps/authorize'
    self.base_url = f'https://{self.host}/api'
    self.token_url = self.base_url + '/oauth/token'
    self.client = self.auth()

  def auth(self):
    """
    Checks if a valid access token exists in the token file;
    if not, tries to get a new one via a refresh token (if present)
    or prompts the user to authenticate in order to get a brand new
    token. 
    """
    logger.debug("Setting up FitTrackee auth")
    if self.tokens is None:
      logger.debug("No FitTrackee tokens found; fetching new ones")
      return self.web_application_flow()
    else:
      logger.debug("Using existing FitTrackee tokens with self-refreshing client")
      return self.get_refreshing_client()

  def web_application_flow(self):
    logger.debug("Running FitTrackee Web Application Flow")
    redirect_uri = f'https://self.host/callback'
    scope = 'workouts:read workouts:write'
    oauth = OAuth2Session(self.client_id, 
                          redirect_uri=redirect_uri,          
                          scope=scope)
    authorization_url, state = oauth.authorization_url(self.authorize_url)
    print(f'Please go to {authorization_url} and authorize access.')
    
    authorization_response = input('\nEnter the full callback URL from the browser address bar after you are redirected and press <enter>:\n\n')
    self.tokens = oauth.fetch_token(self.token_url,
      authorization_response=authorization_response,
      client_secret=self.client_secret, include_client_id=True, verify=False)
  
    save_conf('FITTRACKEE_TOKEN_FILE', self.tokens)
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
      token_updater=lambda x: save_conf('FITTRACKEE_TOKEN_FILE', x))
    return client

  def get_workouts(self, limit: Union[int, None] = 30, start_date: str = None):
    if limit is None:
      logger.debug(
        f'Getting all workouts from FitTrackee (in pages of 30) {f"after {start_date}" if start_date else ""}')
    else:
      logger.debug(f"Getting last {limit} activities (in pages of 30) {f'after {start_date}' if start_date else ''}")
    results = {'pagination': {'has_next': True}}
    page = 1
    workouts = []

    while results['pagination']['has_next'] and (len(workouts) < limit if limit else True):
      r = self.client.get(
        self.base_url + '/workouts', 
        params={'per_page': 30, 'page': page, 'from': start_date},
        verify=False)
      r.raise_for_status()
      results = r.json()
      workouts.extend(results['data']['workouts'])
      logger.debug(f'Fetched page {page} of workouts '
                   f"(fetched {len(workouts)} so far)")
      page += 1
    
    if limit:
      workouts = workouts[:limit]
    
    return workouts

if __name__ == '__main__':
  # strava = StravaConnector()
  # activities = strava.get_activities()
  # for a in activities:
  #   act = strava.create_activity_from_strava(a, get_streams=False)
  #   print(act.start_time, act.activity_dict['id'])
    # logger.debug(f"Writing activity gpx to {tempdir.name}/{act.activity_dict['id']}.gpx")
    # with open(tempdir.name + f'/{act.activity_dict["id"]}.gpx', 'w') as f:
      # f.write(act.as_xml())
  fittrackee = FitTrackeeConnector()
  workouts = fittrackee.get_workouts(limit=None, start_date=None)
  # pprint(fittrackee.client.get(fittrackee.base_url + '/workouts', verify=False).json())
  pass
