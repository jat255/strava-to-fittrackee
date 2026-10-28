import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock

from strava_to_fittrackee.s2f import StravaConnector


class ActivityStreamTests(unittest.TestCase):
    def setUp(self):
        self.connector = StravaConnector.__new__(StravaConnector)
        self.activity = {
            "id": 123,
            "name": "Morning ride",
            "start_date": "2026-01-02T03:04:05Z",
            "type": "Ride",
            "manual": False,
            "gear_id": None,
            "description": None,
            "distance": 1000,
            "moving_time": 60,
        }

    def test_combined_streams_create_gpx_with_one_request(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1]]},
            {"type": "time", "data": [0, 60]},
            {"type": "altitude", "data": [1500, 1510]},
            {"type": "velocity_smooth", "data": [2.0, 3.0]},
            {"type": "distance", "data": [0, 1000]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.connector.make_api_request.assert_called_once()
        args, kwargs = self.connector.make_api_request.call_args
        self.assertEqual(args[0], "/activities/123/streams")
        keys = set(
            ",".join(kwargs["params"]["keys"]).split(",")
        )
        self.assertTrue({"latlng", "time", "altitude", "velocity_smooth", "distance"} <= keys)
        self.assertEqual(activity.distance, [0, 1000])
        points = activity.as_gpx().tracks[0].segments[0].points
        self.assertEqual(len(points), 2)
        self.assertEqual((points[1].latitude, points[1].longitude), (40.1, -105.1))
        self.assertEqual(points[1].elevation, 1510)
        self.assertEqual(points[1].speed, 3.0)
        self.assertEqual(points[1].time, datetime(2026, 1, 2, 3, 4, 5) + timedelta(seconds=60))

    def test_missing_distance_uses_activity_summary(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0]]},
            {"type": "time", "data": [0]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.assertEqual(activity.distance, [0.0, 1000])
        self.assertEqual(activity.altitude, [None])
        self.assertEqual(activity.velocity, [None])


if __name__ == "__main__":
    unittest.main()
