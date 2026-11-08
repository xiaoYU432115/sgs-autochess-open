import unittest

from core import transport_metrics


class TransportMetricsTests(unittest.TestCase):
    def setUp(self):
        transport_metrics.start()

    def test_snapshot_aggregates_body_sizes_by_route(self):
        transport_metrics.record("GET", "/cmd?after=4&session=2", 0, 120, 200, 8)
        transport_metrics.record("POST", "/push", 640, 16, 200, 12)
        transport_metrics.record("GET", "/transport_stats", 0, 80, 200, 1)

        result = transport_metrics.snapshot()

        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["requestBodyBytes"], 640)
        self.assertEqual(result["responseBodyBytes"], 136)
        self.assertEqual(result["handlerP50Ms"], 10)
        self.assertEqual(result["handlerP95Ms"], 12)
        self.assertEqual(result["routes"], [
            {
                "method": "GET",
                "route": "/cmd",
                "requests": 1,
                "requestBodyBytes": 0,
                "responseBodyBytes": 120,
                "handlerP95Ms": 8,
            },
            {
                "method": "POST",
                "route": "/push",
                "requests": 1,
                "requestBodyBytes": 640,
                "responseBodyBytes": 16,
                "handlerP95Ms": 12,
            },
        ])

if __name__ == "__main__":
    unittest.main()
