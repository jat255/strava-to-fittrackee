import unittest

from requests import Response
from requests.exceptions import HTTPError

from strava_to_fittrackee.s2f import TooManyRequestsError, custom_raise_for_status


class StravaResponseTests(unittest.TestCase):
    def response(self, status, headers):
        response = Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = "https://www.strava.com/api/v3/athlete"
        return response

    def test_header_names_are_case_insensitive(self):
        for usage, limit in [
            ("x-ratelimit-usage", "x-ratelimit-limit"),
            ("X-RateLimit-Usage", "X-RateLimit-Limit"),
            ("X-Ratelimit-Usage", "X-Ratelimit-Limit"),
        ]:
            with self.subTest(usage=usage):
                response = self.response(200, {usage: "3,4", limit: "100,1000"})
                with self.assertLogs("s2f", level="DEBUG") as logs:
                    custom_raise_for_status(response)
                self.assertIn("3/100 -- daily: 4/1000", logs.output[0])

    def test_missing_headers_do_not_hide_http_errors(self):
        with self.assertRaises(HTTPError) as error:
            custom_raise_for_status(self.response(401, {}))
        self.assertEqual(error.exception.response.status_code, 401)

    def test_rate_limit_error_does_not_require_headers(self):
        with self.assertRaises(TooManyRequestsError):
            custom_raise_for_status(self.response(429, {}))

    def test_missing_or_malformed_headers_do_not_break_success(self):
        for headers in ({}, {"X-RateLimit-Usage": "invalid", "X-RateLimit-Limit": "100,1000"}):
            with self.subTest(headers=headers):
                custom_raise_for_status(self.response(200, headers))


if __name__ == "__main__":
    unittest.main()
