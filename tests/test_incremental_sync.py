import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests import Response

from strava_to_fittrackee import s2f


def summary(activity_id, date):
    return {"id": activity_id, "start_date": date}


class IncrementalSyncTests(unittest.TestCase):
    def setUp(self):
        self.strava = s2f.StravaConnector.__new__(s2f.StravaConnector)

    def test_limit_across_pages_returns_distinct_newest_activities(self):
        activities = [
            summary(i, "2026-01-%02dT00:00:00Z" % i)
            for i in range(10, 0, -1)
        ]
        requests = []

        def request(path, params):
            requests.append(params.copy())
            offset = (params["page"] - 1) * params["per_page"]
            return Mock(json=Mock(return_value=activities[offset:offset + params["per_page"]]))

        self.strava.make_api_request = request
        self.strava.get_detailed_activity = lambda activity: activity
        result = list(self.strava.get_activities(limit=5, per_page=3))

        self.assertEqual([item["id"] for item in result], [6, 7, 8, 9, 10])
        self.assertEqual([p["per_page"] for p in requests], [3, 3])
        self.assertEqual([p["page"] for p in requests], [1, 2])

    def test_sync_restart_continues_after_last_successful_upload(self):
        activities = [
            summary(3, "2026-01-03T00:00:00Z"),
            summary(2, "2026-01-02T00:00:00Z"),
            summary(1, "2026-01-01T00:00:00Z"),
        ]
        uploaded = []
        fail_once = [True]

        def request(path, params):
            eligible = [
                a for a in activities
                if datetime.strptime(a["start_date"], "%Y-%m-%dT%H:%M:%SZ")
                .replace(tzinfo=timezone.utc).timestamp() > params["after"]
            ]
            return Mock(json=Mock(return_value=eligible if params["page"] == 1 else []))

        self.strava.make_api_request = request
        self.strava.get_detailed_activity = lambda activity: activity
        self.strava.create_activity_from_strava = lambda a, get_streams: SimpleNamespace(
            type="Ride", lat=[None], long=[None], activity_dict=a
        )
        fittrackee = Mock()
        fittrackee.sport_id_map = {"Ride": 1}
        fittrackee.get_workouts.side_effect = lambda **kwargs: (
            [{"workout_date": format_datetime(datetime.strptime(
                uploaded[-1]["start_date"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc))}] if uploaded else []
        )

        def upload(activity):
            if activity.activity_dict["id"] == 2 and fail_once[0]:
                fail_once[0] = False
                raise RuntimeError("simulated interruption")
            uploaded.append(activity.activity_dict)

        fittrackee.upload_no_gpx.side_effect = upload
        with patch.object(s2f, "setup_tempdir"), \
             patch.object(s2f, "StravaConnector", return_value=self.strava), \
             patch.object(s2f, "FitTrackeeConnector", return_value=fittrackee), \
             patch.object(s2f, "activity_has_matching_workout", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                s2f.sync_strava_with_fittrackee()
            self.assertEqual([a["id"] for a in uploaded], [1])
            s2f.sync_strava_with_fittrackee()

        self.assertEqual([a["id"] for a in uploaded], [1, 2, 3])

    def test_rate_limited_request_waits_and_retries(self):
        limited = Response()
        limited.status_code = 429
        success = Response()
        success.status_code = 200
        self.strava.base_url = "https://example.test"
        self.strava.client = Mock()
        self.strava.client.get.side_effect = [limited, success]

        with patch.object(s2f, "wait_until_fifteen") as wait:
            response = self.strava.make_api_request("/athlete/activities", params={"page": 1})

        self.assertIs(response, success)
        wait.assert_called_once_with()
        self.assertEqual(self.strava.client.get.call_count, 2)
        self.strava.client.get.assert_called_with(
            "https://example.test/athlete/activities", params={"page": 1}
        )


if __name__ == "__main__":
    unittest.main()
