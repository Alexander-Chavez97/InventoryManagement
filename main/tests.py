from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse


class HealthzTests(TestCase):
    def test_healthz_is_reachable_without_login(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_healthz_reports_503_when_the_database_is_unreachable(self):
        with patch("main.views.connection") as mock_connection:
            mock_connection.cursor.side_effect = Exception("could not connect to server")
            response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
