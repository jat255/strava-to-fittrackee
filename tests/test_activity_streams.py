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
            "start_latlng": [39.0, -104.0],
            "end_latlng": [39.1, -104.1],
        }
        self.start_time = datetime.strptime("2026-01-02T03:04:05Z", "%Y-%m-%dT%H:%M:%SZ")
        self.end_time = self.start_time + timedelta(seconds=60)

    def test_combined_streams_create_gpx_with_one_request(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1]]},
            {"type": "time", "data": [0, 60]},
            {"type": "altitude", "data": [1500, 1510]},
            {"type": "velocity_smooth", "data": [2.0, 3.0]},
            {"type": "distance", "data": [0, 1000]},
            {"type": "watts", "data": [130, 135]},
            {"type": "cadence", "data": [3, 4]},
            {"type": "heartrate", "data": [120, 123]},
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
        gpx = activity.as_gpx()
        self.assertEqual(gpx.nsmap['gpxtpx'], 'http://www.garmin.com/xmlschemas/TrackPointExtension/v1')
        points = gpx.tracks[0].segments[0].points
        self.assertEqual(len(points), 2)
        self.assertEqual((points[1].latitude, points[1].longitude), (40.1, -105.1))
        self.assertEqual(points[1].elevation, 1510)
        self.assertEqual(points[1].speed, 3.0)
        self.assertEqual(points[1].time, self.end_time)
        self.assertEqual(len(points[1].extensions), 2)
        extensions = []
        for extension in points[1].extensions:
            if "{gpxtpx}TrackPointExtension" == extension.tag:
                extensions.extend(extension)
            else:
                extensions.append(extension)
        self.assertEqual(len(list(filter(lambda extension: extension.tag == "power", extensions))), 1)
        self.assertEqual(len(list(filter(lambda extension: extension.tag == "{gpxtpx}cad", extensions))), 1)
        self.assertEqual(len(list(filter(lambda extension: extension.tag == "{gpxtpx}hr", extensions))), 1)
        self.assertEqual(next(filter(lambda extension: extension.tag == "power", extensions)).text, '135')
        self.assertEqual(next(filter(lambda extension: extension.tag == "{gpxtpx}cad", extensions)).text, '4')
        self.assertEqual(next(filter(lambda extension: extension.tag == "{gpxtpx}hr", extensions)).text, '123')

    def test_extensions_not_added_when_relevant_streams_missing(self):
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
        gpx = activity.as_gpx()
        points = gpx.tracks[0].segments[0].points
        self.assertEqual(len(points), 2)
        self.assertEqual((points[1].latitude, points[1].longitude), (40.1, -105.1))
        self.assertEqual(points[1].elevation, 1510)
        self.assertEqual(points[1].speed, 3.0)
        self.assertEqual(points[1].time, self.end_time)
        self.assertEqual(len(points[1].extensions), 0)

    def test_missing_distance_uses_activity_summary(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1]]},
            {"type": "time", "data": [0, 60]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.assertEqual(activity.time, [self.start_time, self.end_time])
        self.assertEqual(activity.distance, [0.0, 1000])
        self.assertEqual(activity.lat, [40.0, 40.1])
        self.assertEqual(activity.long, [-105.0, -105.1])
        self.assertEqual(activity.altitude, [None, None])
        self.assertEqual(activity.velocity, [None, None])

    def test_stream_lengths_are_normalized_when_there_is_one_time(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1]]},
            {"type": "time", "data": [0]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.assertEqual(activity.time, [self.start_time])
        self.assertEqual(activity.distance, [None])
        self.assertEqual(activity.lat, [40.0])
        self.assertEqual(activity.long, [-105.0])
        self.assertEqual(activity.altitude, [None])
        self.assertEqual(activity.velocity, [None])

    def test_stream_lengths_are_normalized_when_there_there_are_two_times(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1], [40.2, -105.2]]},
            {"type": "time", "data": [0, 60]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.assertEqual(activity.time, [self.start_time, self.end_time])
        self.assertEqual(activity.distance, [0, 1000])
        self.assertEqual(activity.lat, [39.0, 39.1])
        self.assertEqual(activity.long, [-104.0, -104.1])
        self.assertEqual(activity.altitude, [None, None])
        self.assertEqual(activity.velocity, [None, None])

    def test_stream_lengths_are_normalized_when_there_there_are_more_than_two_times(self):
        streams = [
            {"type": "latlng", "data": [[40.0, -105.0], [40.1, -105.1], [40.2, -105.2], [40.3, -105.3]]},
            {"type": "time", "data": [0, 60, 120]},
        ]
        self.connector.make_api_request = Mock(return_value=Mock(json=Mock(return_value=streams)))

        activity = self.connector.create_activity_from_strava(self.activity)

        self.assertEqual(activity.time, [self.start_time, self.end_time, self.start_time + timedelta(seconds=120)])
        self.assertEqual(activity.distance, [None, None, None])
        self.assertEqual(activity.lat, [None, None, None])
        self.assertEqual(activity.long, [None, None, None])
        self.assertEqual(activity.altitude, [None, None, None])
        self.assertEqual(activity.velocity, [None, None, None])

if __name__ == "__main__":
    unittest.main()
