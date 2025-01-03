"""
This tool provides functionality to download activities from a Strava "athlete" and
upload them as workouts to a FitTrackee instance (see README.md for more details)

Examples (in your terminal):
    $ python -m strava_to_fittrackee.s2f --sync
    $ python -m strava_to_fittrackee.s2f --download-all-strava --output-folder <folder_name>
    $ python -m strava_to_fittrackee.s2f --upload-all-fittrackee --input-folder <folder_name>
    $ python -m strava_to_fittrackee.s2f --delete-all-fittrackee

Copyright (c) 2022-2023, Joshua Taillon
"""
import argparse
import atexit
import csv
import json
import importlib.metadata
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import gpxpy
import pytz
import urllib3
from dotenv import load_dotenv
from requests import Response
from requests.exceptions import HTTPError
from requests_oauthlib import OAuth2Session
from tqdm import tqdm

logger = logging.getLogger("s2f")
logging.basicConfig()
logger.setLevel(logging.DEBUG)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
script_dir = Path(__file__).parent

__version__ = importlib.metadata.version("strava_to_fittrackee")

def setup_logging(level: int = 2):
    level_map = {0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}
    logger.setLevel(level_map[level])


def log_and_delete_file(f: Path):
    logger.debug(f"Removing {f}")
    f.unlink()


def check_for_running_instance():
    pid = str(os.getpid())
    pidfile = script_dir / "s2f.pid"
    if pidfile.exists():
        logger.error(f"PID file {pidfile} already exists; exiting!")
        raise RuntimeError(
            f"Exiting because a lock file exists; if you are sure no other "
            f"instances are running,\nplease delete {pidfile.resolve()} manually"
        )
    else:
        with open(pidfile, "w") as f:
            f.write(pid)
        atexit.register(lambda: log_and_delete_file(pidfile))


def setup_tempdir():
    # create directory for storing files temporarily and delete it
    # when the program finishes (using atexit module)
    global tempdir
    tempdir = tempfile.TemporaryDirectory()
    logger.debug(f"Creating tempdir: {tempdir}")
    atexit.register(lambda: logger.debug(f"Removing {tempdir}") and tempdir.cleanup())


def cmdline_args():
    # Make parser object
    p = argparse.ArgumentParser(
        description=__doc__ + f"v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    p.add_argument(
        "-v",
        "--verbosity",
        type=int,
        choices=[0, 1, 2],
        default=1,
        help="increase output verbosity (default: %(default)s)",
    )

    group1 = p.add_mutually_exclusive_group(required=True)
    
    group1.add_argument(
        "-V",
        "--version",
        help="display the program's version",
        action="store_true"
    )

    group1.add_argument(
        "--setup-tokens",
        help=(
            "Setup initial token authentication for both Strava and"
            " FitTrackee. This will be done automatically if they are not"
            " already set up, but this option allows you to do that without"
            " performing any other actions"
        ),
        action="store_true",
    )
    group1.add_argument(
        "--sync",
        help=(
            "Download activities from Strava not currently present in "
            "FitTrackee and upload them to the FitTrackee instance"
        ),
        action="store_true",
    )
    group1.add_argument(
        "--download-all-strava",
        action="store_true",
        help=(
            "Download and store all Strava activities as GPX files in the "
            'given folder (default: "./gpx/", but can be changed with '
            '"--output-folder" option)'
        ),
    )
    group1.add_argument(
        "--upload-all-fittrackee",
        action="store_true",
        help=(
            "Upload all GPX files in the given folder as workouts to the "
            'configured FitTrackee instance. (default folder is "./gpx/", '
            'but can be configured with the "--input-folder" option)'
        ),
    )
    group1.add_argument(
        "--delete-all-fittrackee",
        action="store_true",
        help="Delete all workouts in the configured FitTrackee instance",
    )
    group1.add_argument(
        "--upload-gpx",
        action="store",
        dest="gpx_file",
        help=("Can be used to upload a single GPX file to the FitTrackee" " instance"),
    )
    p.add_argument(
        "--output-folder",
        action="store",
        default="./gpx",
        help=(
            "Folder in which to store GPX files generated from Strava"
            ' activities (if the "--download-all-strava" option is given;'
            ' default: "%(default)s")'
        ),
    )
    p.add_argument(
        "--input-folder",
        action="store",
        default="./gpx",
        help=(
            "Folder in which to find GPX files to be uploaded to FitTrackee "
            '(if the "--upload-all-fittrackee" option is given; '
            'default: "%(default)s")'
        ),
    )

    return p.parse_args()


class TooManyRequestsError(HTTPError):
    """Error to throw when a 429 status is returned"""


def custom_raise_for_status(r: Response, log_api_usage: bool = True):
    """
    Parses a Strava API response, logs the current API usage and limits
    (if requested), raises a custom error if the request is over the API
    limits, and then calls the requests module's ``raise_for_status()``
    """
    fifteen_usage, daily_usage = dict(r.headers)["x-ratelimit-usage"].split(",")
    fifteen_limit, daily_limit = dict(r.headers)["x-ratelimit-limit"].split(",")
    if log_api_usage:
        logger.debug(
            "Current API usage -- 15 minute:"
            f" {fifteen_usage}/{fifteen_limit} -- daily:"
            f" {daily_usage}/{daily_limit}"
        )
    if r.status_code == 429:
        raise TooManyRequestsError("429 Too Many Requests")
    r.raise_for_status()


def get_or_raise_env(value: str, allow_none: bool = False) -> Union[str, None]:
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
        raise EnvironmentError(
            f"No {value} defined in environment, "
            "please check the .env file (or README.md "
            "for more info)"
        )
    return val


def load_conf(env_var):
    token_file = get_or_raise_env(env_var)
    token_path = Path(token_file)
    if token_path.exists():
        with open(token_path, "r") as f:
            tokens = json.load(f)
    else:
        tokens = None
    return tokens


def save_conf(env_var, tokens):
    logger.debug(f"Saving tokens for {env_var}")
    token_file = get_or_raise_env(env_var)
    with open(Path(token_file), "w") as f:
        json.dump(tokens, f, indent=2)


class StravaConnector:
    def __init__(self):
        logger.debug("Initializing StravaConnector")
        self.tokens = load_conf("STRAVA_TOKEN_FILE")
        self.client_id = get_or_raise_env("STRAVA_CLIENT_ID")
        self.client_secret = get_or_raise_env("STRAVA_CLIENT_SECRET")
        self.authorize_url = "https://www.strava.com/oauth/authorize"
        self.base_url = "https://www.strava.com/api/v3"
        self.token_url = self.base_url + "/oauth/token"
        self.client = self.auth()
        self.gear = {}

    def web_application_flow(self):
        logger.debug("Running Web Application Flow")
        redirect_uri = "https://localhost"
        scope = ["activity:read_all"]
        oauth = OAuth2Session(self.client_id, redirect_uri=redirect_uri, scope=scope)
        authorization_url, state = oauth.authorization_url(self.authorize_url)
        print(f"\nPlease go to {authorization_url} and authorize access.")

        authorization_response = input(
            "\nEnter the full callback URL from the browser address bar after"
            " you are redirected and press <enter>:\n\n"
        )
        self.tokens = oauth.fetch_token(
            self.token_url,
            authorization_response=authorization_response,
            client_secret=self.client_secret,
            include_client_id=True,
        )

        save_conf("STRAVA_TOKEN_FILE", self.tokens)
        return oauth

    def get_refreshing_client(self):
        refresh_params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        client = OAuth2Session(
            self.client_id,
            token=self.tokens,
            auto_refresh_url=self.token_url,
            auto_refresh_kwargs=refresh_params,
            token_updater=lambda x: save_conf("STRAVA_TOKEN_FILE", x),
        )
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

    def get_detailed_activity(
        self,
        activity_dict: Dict
    ):
        """
        Get the details for a specific activity from the Strava API.

        Given an activity response from the ``/athlete/activities`` endpoint,
        get the DetailedActivity
        (https://developers.strava.com/docs/reference/#api-models-DetailedActivity)
        representation of that activity (only used for getting the description,
        currently).

        Parameters:
        -----------
        activity_dict:
            A dictionary representation of a SummaryActivity from the Strava
            API (https://developers.strava.com/docs/reference/#api-models-SummaryActivity)
        """
        success = False
        while not success:
            try:
                logger.debug(
                    f"Getting activity details for activity {activity_dict['id']}"
                )
                r = self.client.get(
                    self.base_url + f"/activities/{activity_dict['id']}"
                )
                custom_raise_for_status(r)
                success = True
            except TooManyRequestsError:
                logger.warning(
                    "Hit Strava API limit; sleeping until next 15 minute interval"
                )
                wait_until_fifteen()
        return r.json()

    def get_activities(
        self,
        limit: Union[int, None] = 30,
        after: Optional[datetime] = None,
        per_page: int = 30,
    ):
        """
        If ``limit`` is ``None``, get all activities available (useful for initial
        run, perhaps), otherwise get a limited number (default: 30)

        Parameters
        ----------
        limit:
            The upper limit of the number of activities that will be returned
        after:
            If provided, only return activities after this point in time
        per_page:
            How many activiries to download per request to the API (larger values take
            longer but require fewer requests from the API)
        """
        if limit is None:
            logger.debug(
                "Getting all Strava activities" f' {f"after {after}" if after else ""}'
            )
            page = 1
            all_activities = []
            while True:
                params = {"per_page": per_page, "page": page}
                if after:
                    params["after"] = after.timestamp()
                success = False
                while not success:
                    try:
                        r = self.client.get(
                            self.base_url + "/athlete/activities", params=params
                        )
                        custom_raise_for_status(r)
                        success = True
                    except TooManyRequestsError:
                        logger.warning(
                            "Hit Strava API limit; sleeping until next 15 minute interval"
                        )
                        wait_until_fifteen()
                if len(r.json()) == 0:
                    logger.debug(
                        "No more activities found "
                        f"(total activities: {len(all_activities)})"
                    )
                    return [self.get_detailed_activity(a) for a in all_activities]
                else:
                    all_activities.extend(r.json())
                    logger.debug(
                        f"Fetched page {page} of activities "
                        f"(fetched {len(all_activities)} so far)"
                    )
                    page += 1
        else:
            logger.debug(
                f"Getting last {limit} activities"
                f' {f"after {after}" if after else ""}'
            )
            params = {"per_page": limit}
            if after:
                params["after"] = after.timestamp()
            success = False
            while not success:
                try:
                    r = self.client.get(
                        self.base_url + "/athlete/activities", params=params
                    )
                    custom_raise_for_status(r)
                    success = True
                except TooManyRequestsError:
                    logger.warning(
                        "Hit Strava API limit; sleeping until next 15 minute interval"
                    )
                    wait_until_fifteen()
            activities = r.json()
            return [self.get_detailed_activity(a) for a in activities]

    def get_gear(self, gear_id: str) -> Dict:
        """
        Get gear definition from local store, or API if necessary.
        
        Takes a gear identifier (string) and will return the dict returned by the Strava
        API for that gear. It will cache the result locally in this connector to save
        network resources on subsequent queries

        Parameters
        ----------
        gear_id
            The identifier string for a piece of gear (as used in the activity response)

        Returns
        -------
        dict
            The API response for this piece of gear
        """
        if gear_id in self.gear:
            return self.gear[gear_id]
        r = self.client.get(
            self.base_url + f"/gear/{gear_id}"
        )
        custom_raise_for_status(r)
        self.gear[gear_id] = r.json()

        return self.gear[gear_id]

    def filter_response_by_key(
        self,
        response: List[Dict],
        type_key: str,
        null_return: Any
    ):
        if not response:
            return null_return
        
        response = list(filter(lambda d: d['type'] == type_key, response))
        
        return response[0]['data'] if response else null_return
        

    def create_activity_from_strava(self, activity: dict, get_streams: bool = True):
        activity_id = activity["id"]
        if activity["manual"] and get_streams:
            logger.warning(
                f"Strava activity {activity_id} had no GPS data; cannot download GPX!"
            )
            get_streams = False

        if get_streams:
            logger.debug(f"Getting latitude and longitude for activity {activity_id}")
            r = self.client.get(
                self.base_url + f"/activities/{activity_id}/streams",
                params={"keys": ["latlng"]},
            )
            custom_raise_for_status(r)
            latlng = self.filter_response_by_key(r.json(), 'latlng', [(None, None)])
            distance = self.filter_response_by_key(r.json(), 'distance', [0.0, activity['distance']])

            logger.debug(f"Getting timepoints for activity {activity_id}")
            r = self.client.get(
                self.base_url + f"/activities/{activity_id}/streams",
                params={"keys": ["time"]},
            )
            custom_raise_for_status(r)
            time_list = self.filter_response_by_key(r.json(), 'time', [0.0, activity['moving_time']])

            logger.debug(f"Getting altitude for activity {activity_id}")
            r = self.client.get(
                self.base_url + f"/activities/{activity_id}/streams",
                params={"keys": ["altitude"]},
            )
            custom_raise_for_status(r)
            altitude = self.filter_response_by_key(r.json(), 'altitude', [None])


            logger.debug(f"Getting velocity for activity {activity_id}")
            r = self.client.get(
                self.base_url + f"/activities/{activity_id}/streams",
                params={"keys": ["velocity_smooth"]},
            )
            custom_raise_for_status(r)
            velocity = self.filter_response_by_key(r.json(), 'velocity_smooth', [None])
        else:
            latlng = [(None, None)]
            distance = [0.0, activity['distance']]
            time_list = [0, activity['moving_time']]
            altitude = [None]
            velocity = [None]

        # process gear (will be None if no gear defined on activity)
        gear = self.get_gear(activity["gear_id"]) if activity["gear_id"] else None

        description = activity["description"]
        description = "" if description is None else description
        
        return Activity(
            activity_dict=activity,
            latlng=latlng,
            time_list=time_list,
            altitude=altitude,
            velocity=velocity,
            distance=distance,
            gear=gear,
            description=description
        )


class Activity:
    def __init__(
        self,
        activity_dict,
        latlng,
        time_list,
        altitude,
        velocity,
        distance,
        gear,
        description,
    ):
        self.title = activity_dict["name"]
        self.activity_dict = activity_dict
        self.start_time = datetime.strptime(
            activity_dict["start_date"], "%Y-%m-%dT%H:%M:%SZ"
        )
        self.lat = [i[0] for i in latlng]
        self.long = [i[1] for i in latlng]
        self.time = [(self.start_time + timedelta(seconds=t)) for t in time_list]
        self.altitude = altitude
        self.velocity = velocity
        self.distance = distance
        self.type = activity_dict["type"]
        self.link = f"https://strava.com/activities/{activity_dict['id']}"
        self.gear = gear
        self.gear_note = self.get_gear_note()
        self.description = description

    def as_dict(self) -> Dict:
        return {
            'title': self.title,
            'activity_dict': self.activity_dict,
            'start_time': self.start_time.isoformat(),
            'lat': self.lat,
            'long': self.long,
            'time': [i.isoformat() for i in self.time],
            'altitude': self.altitude,
            'velocity': self.velocity,
            'distance': self.distance,
            'type': self.type,
            'link': self.link,
            'gear': self.gear,
            'gear_note': self.gear_note,
            'description': self.description,
        }

    def as_gpx(self) -> gpxpy.gpx.GPX:
        """
        Build this activity and its geo representation as GPX

        Taken partially from https://stackoverflow.com/a/70665366
        """
        gpx = gpxpy.gpx.GPX()

        # Create first track in our GPX:
        gpx_track = gpxpy.gpx.GPXTrack(
            name=self.title, description=self.type
        )
        gpx_track.link = self.link
        # store activity json as comment in gpx_track
        gpx_track.comment = json.dumps(self.as_dict())
        gpx.tracks.append(gpx_track)

        # Create first segment in our GPX track:
        gpx_segment = gpxpy.gpx.GPXTrackSegment()
        gpx_track.segments.append(gpx_segment)

        # Create points:
        for time, lat, long, alt, vel in zip(
            self.time, self.lat, self.long, self.altitude, self.velocity
        ):
            gpx_segment.points.append(
                gpxpy.gpx.GPXTrackPoint(lat, long, elevation=alt, time=time, speed=vel)
            )

        return gpx

    def as_xml(self):
        """Export this activity's GPX (XML) representation as a string."""
        return self.as_gpx().to_xml()

    def get_gear_note(self):
        """Get description of gear (if any) for the notes field."""
        if self.gear is None:
            return ""
        
        note = (
            f"\n\nGear used: {self.gear['name']} "
            f"(cumulative distance: {self.gear['converted_distance']})"
        )
        return note



class FitTrackeeConnector:
    """
    This class has a lot of overlap with the StravaConnector code, and the
    two should be refactored into sub-classes of one common base, but that
    is more work than I care to do upon initial writing.
    """
    def __init__(self, verify=False):
        logger.debug("Initializing FitTrackeeConnector")
        self.tokens = load_conf("FITTRACKEE_TOKEN_FILE")
        self.host = get_or_raise_env("FITTRACKEE_HOST")
        self.verify = verify
        self.client_id = get_or_raise_env("FITTRACKEE_CLIENT_ID")
        self.client_secret = get_or_raise_env("FITTRACKEE_CLIENT_SECRET")
        self.authorize_url = f"https://{self.host}/profile/apps/authorize"
        self.base_url = f"https://{self.host}/api"
        self.token_url = self.base_url + "/oauth/token"
        self.client = self.auth()
        self.sports = None
        self.timezone = None

        # Mapping from Strava activity types to FitTrackee workout sport id values
        # use first sport id if we don't have a description
        # (will be wrong, but better than error)
        self.sport_id_map = {
            None: 1,
            "Ride": self.get_sport_id("Cycling (Sport)"),
            "VirtualRide": self.get_sport_id("Cycling (Virtual)"),
            "Hike": self.get_sport_id("Hiking"),
            "Walk": self.get_sport_id("Walking"),
            "MountainBikeRide": self.get_sport_id("Mountain Biking"),
            "EMountainBikeRide": self.get_sport_id("Mountain Biking (Electric)"),
            "Rowing": self.get_sport_id("Rowing"),
            "Run": self.get_sport_id("Running"),
            "AlpineSki": self.get_sport_id("Skiing (Alpine)"),
            "NordicSki": self.get_sport_id("Skiing (Cross Country)"),
            "Snowshoe": self.get_sport_id("Snowshoes"),
            "TrailRun": self.get_sport_id("Trail"),
        }

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
        redirect_uri = f"https://self.host/callback"
        scope = "workouts:read workouts:write profile:read"
        oauth = OAuth2Session(self.client_id, redirect_uri=redirect_uri, scope=scope)
        authorization_url, state = oauth.authorization_url(self.authorize_url)
        print(f"\nPlease go to {authorization_url} and authorize access.")

        authorization_response = input(
            "\nEnter the full callback URL from the browser address bar after"
            " you are redirected and press <enter>:\n\n"
        )
        self.tokens = oauth.fetch_token(
            self.token_url,
            authorization_response=authorization_response,
            client_secret=self.client_secret,
            include_client_id=True,
            verify=self.verify,
        )

        save_conf("FITTRACKEE_TOKEN_FILE", self.tokens)
        return oauth

    def get_refreshing_client(self):
        refresh_params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        client = OAuth2Session(
            self.client_id,
            token=self.tokens,
            auto_refresh_url=self.token_url,
            auto_refresh_kwargs=refresh_params,
            token_updater=lambda x: save_conf("FITTRACKEE_TOKEN_FILE", x),
        )
        return client

    def get_workouts(
        self,
        limit: Union[int, None] = 30,
        start_date: str = None,
        end_date: str = None,
    ):
        if limit is None:
            logger.debug(
                "Getting all workouts from FitTrackee (in pages of 30)"
                f' {f"after {start_date}" if start_date else ""}'
            )
        else:
            logger.debug(
                f"Getting last {limit} activities (in pages of 30)"
                f" {f'after {start_date}' if start_date else ''}"
            )
        results = {"pagination": {"has_next": True}}
        page = 1
        workouts = []

        while results["pagination"]["has_next"] and (
            len(workouts) < limit if limit else True
        ):
            r = self.client.get(
                self.base_url + "/workouts",
                params={
                    "per_page": 30,
                    "page": page,
                    "from": start_date,
                    "to": end_date,
                },
                verify=self.verify,
            )
            r.raise_for_status()
            results = r.json()
            workouts.extend(results["data"]["workouts"])
            logger.debug(
                f"Fetched page {page} of workouts " f"(fetched {len(workouts)} so far)"
            )
            page += 1

        if limit:
            workouts = workouts[:limit]

        return workouts

    def get_sports(self):
        logger.debug(f"Getting sport types")
        r = self.client.get(self.base_url + "/sports", verify=self.verify)
        r.raise_for_status()
        return r.json()["data"]["sports"]

    def get_sport_id(self, sport_name: str) -> Union[int, None]:
        if self.sports is None:
            self.sports = self.get_sports()
        sport_dict = list(
            filter(lambda sport: sport["label"] == sport_name, self.sports)
        )
        if sport_dict:
            return sport_dict[0]["id"]
        else:
            return None
    
    def get_user_timezone(self, force_update=False):
        """Get the user timezone from the API and store it as attribute."""
        if self.timezone is None or force_update:
            r = self.client.get(self.base_url + "/auth/profile", verify=self.verify)
            r.raise_for_status()
            self.timezone = r.json()['data']['timezone']

        return self.timezone
    
    def localize_utc_datetime(self, dt: datetime):
        """
        Given a UTC datetime, localize it to the timezone of the user.
        """
        tz = pytz.timezone(self.get_user_timezone())
        localized_dt = pytz.UTC.localize(dt).astimezone(tz)
        return localized_dt

    def upload_gpx(self, gpx_file: Union[str, Path]):
        """
        POST a workout to the FitTrackee API
        https://samr1.github.io/FitTrackee/api/workouts.html#post--api-workouts
        """
        types_by_time = {}

        # this is a temporary fix for activities that are mislabeled in my Strava
        # that I manually corrected in FitTrackee, but then had to delete
        if os.path.isfile("correct_sport_types.csv"):
            with open("correct_sport_types.csv", "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    types_by_time[row["workout_date"]] = int(row["sport_id"])

        if not os.path.exists(str(gpx_file)):
            raise FileNotFoundError(
                f'gpx file: "{gpx_file}" was not found. Please check the file' " exists"
            )

        # get "desc" parameter, assuming it holds the Strava activity type
        with open(gpx_file, "r") as f:
            gpx = gpxpy.parse(f)

        if gpx.tracks:
            activity_type = gpx.tracks[0].description
            url = gpx.tracks[0].link
            activity_dict = json.loads(gpx.tracks[0].comment)
            gpx.tracks[0].comment = None
        else:
            activity_type = None
            url = None
            activity_dict = None
            logger.warning(
                "Did not find activity type; will use sport_id = 1 which might"
                " be incorrect"
            )
        
        # Mapping from Strava activity types to FitTrackee workout sport id values
        # use first sport id if we don't have a description
        # (will be wrong, but better than error)
        data = {
            "sport_id": self.sport_id_map[activity_type],
            "notes": (
                "Uploaded with Strava-to-FitTrackee\nOriginal activity type"
                f' on Strava was "{activity_type}"'
            ),
        }
        gpx_start_time = (
            gpx.tracks[0].segments[0].points[0].time.strftime("%Y-%m-%d %H:%M:%S.000")
        )
        if (
            gpx_start_time in types_by_time
            and data["sport_id"] != types_by_time[gpx_start_time]
        ):
            logger.info(
                f"Overriding {gpx_start_time} activity's sport_id from"
                f' {data["sport_id"]} to {types_by_time[gpx_start_time]}'
            )
            data["sport_id"] = types_by_time[gpx_start_time]

        if url:
            data["notes"] += f"\nOriginal Strava link: {url}"
        
        if activity_dict:
            logger.info("Rewriting GPX file without comment field")
            with open(gpx_file, "w") as f:
                print(gpx.to_xml(), file=f)
            data["notes"] += activity_dict['gear_note']
            data["notes"] += "\n\nStrava description:\n" + \
                activity_dict['description'].replace('\r\n', '\n')

        logger.debug(f"POSTing {gpx_file} to FitTrackee")
        r = self.client.post(
            self.base_url + "/workouts",
            files=dict(file=open(gpx_file, "r")),
            data=dict(data=json.dumps(data)),
            verify=self.verify,
        )
        r.raise_for_status()


    def upload_no_gpx(self, activity: Activity):
        """
        POST a workout to the FitTrackee API without any GPS data (manual activity)
        https://samr1.github.io/FitTrackee/api/workouts.html#post--api-workouts
        """
        activity_type = activity.type
        url = activity.link
        if not activity_type:
            logger.warning(
                "Did not find activity type; will use sport_id = 1 which might"
                " be incorrect"
            )

        # need to localize activity.start_time, which is in UTC
        workout_dt = self.localize_utc_datetime(activity.start_time)
        workout_date = workout_dt.strftime("%Y-%m-%d %H:%M")

        data = {
            "sport_id": self.sport_id_map[activity_type],
            "notes": (
                "Uploaded with Strava-to-FitTrackee\nOriginal activity type"
                f' on Strava was "{activity_type}"'
            ),
            "title": activity.title,
            "distance": activity.distance[-1] / 1000.0,
            "duration": (activity.time[-1] - activity.time[0]).seconds,
            "workout_date": workout_date
        }

        if url:
            data["notes"] += f"\nOriginal Strava link: {url}"

        data["notes"] += activity.gear_note

        if activity.description:
            data["notes"] += "\n\nStrava description:\n" + \
                activity.description.replace('\r\n', '\n')

        logger.debug(f"POSTing workout with no GPX to FitTrackee")
        r = self.client.post(
            self.base_url + "/workouts/no_gpx",
            json=data,
            verify=self.verify,
        )
        r.raise_for_status()


def wait_until_fifteen():
    """Will sleep the thread until the next 15 minute interval"""
    now = datetime.now()
    wait_until = now + (datetime.min - now) % timedelta(minutes=15)
    logger.warning(
        f"Time is now {now.isoformat()}; Sleeping until at least"
        f" {wait_until.isoformat()}"
    )
    while datetime.now() < wait_until:
        time.sleep(10)
    logger.warning(f"Finished sleeping; time is now {datetime.now().isoformat()}")


def activity_has_matching_workout(
    strava: StravaConnector, fittrackee: FitTrackeeConnector, activity: dict
) -> bool:
    """
    Helper function to check if there is a workout in the FitTrackee instance
    within 5 minutes of the Strava activity in question. Helps to prevent
    duplicates from being uploaded into FitTrackee.

    ``activity`` should be a single activity dictionary as returned by the
    ``StravaConnector.get_activities()`` method
    """
    activity_dt = datetime.strptime(activity["start_date"], "%Y-%m-%dT%H:%M:%SZ")
    activity_dt = fittrackee.localize_utc_datetime(activity_dt)
    activity_date_str = activity_dt.strftime("%Y-%m-%d")

    lower_window = activity_dt - timedelta(minutes=5)
    upper_window = activity_dt + timedelta(minutes=5)
    # search for FitTrackee workouts on same day
    same_day_workouts = fittrackee.get_workouts(
        limit=None, start_date=activity_date_str, end_date=activity_date_str
    )
    overlapping_workouts = list(
        filter(
            lambda w: lower_window
            < parsedate_to_datetime(w["workout_date"])
            < upper_window,
            same_day_workouts,
        )
    )
    return len(overlapping_workouts) > 0


def download_all_strava_gpx(folder_name: str):
    """
    This method, useful the first time this tool is used, will download all
    the athelete's activties from Strava and store the tracks as GPX files
    in the specified directory. Depending on the number of activities present,
    this will likely cause the code to go over the Strava API rate limits
    (currently 100 requests per 15 minutes / 1000 requests per day), since
    each activity requires 4 requests to get the location data required to
    build a GPX file. So, if you have more than about 250 activities or so,
    this process will take multiple days -- blame Strava's rate limits!

    To workaround this, the code will skip any activities that already have
    a downloaded GPX file present in the specified folder, and will automatically
    back-off while running and sleep until the next 15 minute interval. There
    is no functionality to deal with the 1000 request limit, so if that gets hit,
    it will just continue trying every fifteen minutes (although it should start
    working on the next day if you let it continue running). Alternatively,
    you can kill the program and restart it the next day manually, and
    already-downloaded activities will be skipped.
    """
    strava = StravaConnector()
    activities = strava.get_activities(limit=None, per_page=200)
    i = 0
    output_folder = Path(folder_name)
    output_folder.mkdir(exist_ok=True)
    while i < len(activities):
        a = activities[i]
        try:
            output_file = (
                output_folder
                / f"{datetime.strptime(a['start_date'], '%Y-%m-%dT%H:%M:%SZ').strftime('%Y%m%d_%H%M%S')}_{a['id']}.gpx"
            )
            if not output_file.exists():
                logger.debug(f"Writing activity gpx to {output_file}")
                if a["manual"] is False:
                    act = strava.create_activity_from_strava(a, get_streams=True)
                    with open(output_file, "w") as f:
                        f.write(act.as_xml())
                else:
                    logger.warning(
                        f"Activity {a['id']} does not have GPS data, skipping!"
                    )
                    with open(str(output_file) + ".json", "w") as f:
                        # print(f"Manual activity {a['id']}", file=f)
                        print(json.dumps(a, indent=2), file=f)
            else:
                logger.debug(f"Output {output_file} already exists, skipping!")
            i += 1
            logger.info(f"Processed {i} of {len(activities)} Activities")
        except TooManyRequestsError:
            logger.warning(
                "Hit Strava API limit; sleeping until next 15 minute interval"
            )
            wait_until_fifteen()


def upload_all_fittrackee(folder_name: str):
    """
    This method, useful the first time this tool is used, will upload all
    GPX files in the specified directory to FitTrackee. It won't check for
    duplicates; fair warning!
    """
    fittrackee = FitTrackeeConnector()
    p = Path(folder_name)
    files = list(p.glob("*.gpx"))
    for f in tqdm(files, desc="Uploading GPX files"):
        fittrackee.upload_gpx(f)


def delete_all_fittrackee():
    """
    This method will delete all workouts in the configured FitTrackee instance.
    Only run this if you really want this!
    """
    fittrackee = FitTrackeeConnector()
    workouts = fittrackee.get_workouts(limit=None)
    print(
        f"This will delete all {len(workouts)} workouts in the configured"
        " FitTrackee instance!"
    )
    if ask_user_to_confirm():
        for w in tqdm(workouts, desc="Deleting workouts"):
            r = fittrackee.client.delete(f"{fittrackee.base_url}/workouts/{w['id']}")
            r.raise_for_status()
    else:
        print("Action was cancelled due to user input")


def sync_strava_with_fittrackee():
    """
    Syncs latest Strava activities with FitTrackee. Will look for Strava
    activities occuring *after* the last FitTrackee workout, so cannot be
    used for retroactive syncing. (see ``download_all_strava_gpx()`` if you
    need that). Will download any new Strava activities as GPX files, save
    them to a temporary folder, and then upload those GPX files to FitTrackee.
    The GPX files are deleted after the program exits.
    """
    setup_tempdir()
    strava = StravaConnector()
    fittrackee = FitTrackeeConnector()

    # get the latest workout from fittrackee, since we only want Strava activities after that time
    latest_workout = fittrackee.get_workouts(limit=1)
    if len(latest_workout) > 0:
        latest_workout = latest_workout[0]

        # use email.utils function to parse RFC 822 style date string from fittrackee API
        latest_dt = parsedate_to_datetime(latest_workout["workout_date"])
        logger.info(f"Last FitTrackee workout was {latest_dt.isoformat()}")
    else:
        # there were no FitTrackee Workouts present, so get minimum datetime
        latest_workout = None
        latest_dt = datetime.fromtimestamp(0)
        logger.info(f"No FitTrackee workouts were found, so syncing all!")

    # get strava activities after the latest fittrackee
    activities = strava.get_activities(after=latest_dt, limit=None)
    logger.info(
        f"Found {len(activities)} Strava activities after " f"{(latest_dt).isoformat()}"
    )
    to_process = []  # list to hold activities that don't exist in fittrackee
    for a in activities:
        if not activity_has_matching_workout(strava, fittrackee, a):
            logger.debug(
                f'Marking Strava activitiy {a["id"]} at {a["start_date"]} as'
                " needing to be processed"
            )
            to_process.append(a)
        else:
            # skip this activity since it already has a matching workout
            logger.debug(
                f'Strava activity {a["id"]} at {a["start_date"]} has a match'
                " in FitTrackee"
            )
    if len(to_process) > 0:
        i = 0
        while i < len(to_process):
            try:
                a = to_process[i]
                # generate GPX and upload to FitTrackee
                logger.debug(f'Processing Strava activity {a["id"]}')
                act = strava.create_activity_from_strava(a, get_streams=True)
                
                # skip this activity if we don't know its sport type
                if act.type not in fittrackee.sport_id_map:
                    logger.warning(
                        f"Activity type {act.type} not recognized in FitTrackee, skipping!"
                    )
                    i += 1
                    continue
                
                if act.lat == [None] and act.long == [None]:
                    # we don't have any GPS data, so do manual activity
                    fittrackee.upload_no_gpx(act)
                else:
                    temp_file = tempdir.name + f'/{act.activity_dict["id"]}.gpx'
                    logger.debug(f"Writing Strava activity gpx to {temp_file}")
                    with open(temp_file, "w") as f:
                        f.write(act.as_xml())
                    logger.info(
                        f"Uploading workout {i+1} of {len(to_process)} to FitTrackee"
                    )
                    logger.debug(f"Uploading {temp_file} to FitTrackee")
                    fittrackee.upload_gpx(temp_file)
                i += 1
            except TooManyRequestsError:
                logger.warning(
                    "Hit Strava API limit; sleeping until next 15 minute interval"
                )
                wait_until_fifteen()
        logger.info(
            f"Processed {len(to_process)} Strava activities to FitTrackee workouts"
        )
    else:
        logger.info("Nothing to do!")


def ask_user_to_confirm():
    """
    Helper method to show an interactive confirmation warning to the user and
    return their response.
    """
    while True:
        confirm = input("Are you sure you want to do this? [y]es or [n]o: ")
        if confirm.lower() == "y":
            return True
        elif confirm.lower() == "n":
            return False
        else:
            print("\n Invalid Option. Please Enter a Valid Option.")


if __name__ == "__main__":
    args = cmdline_args()
    if args.version:
        print(f"strava-to-fittrackee v{__version__}")
    setup_logging(args.verbosity)
    check_for_running_instance()
    if args.setup_tokens:
        logger.info("Setting up Strava tokens...")
        StravaConnector()
        logger.info("Setting up FitTrackee tokens...")
        FitTrackeeConnector()
    if args.download_all_strava:
        download_all_strava_gpx(args.output_folder)
    if args.upload_all_fittrackee:
        upload_all_fittrackee(args.input_folder)
    if args.delete_all_fittrackee:
        delete_all_fittrackee()
    if args.sync:
        sync_strava_with_fittrackee()
    if args.gpx_file:
        FitTrackeeConnector().upload_gpx(args.gpx_file)
