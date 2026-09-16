from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from inventory.models import Item


def jpeg_file(name="shot.jpg"):
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "orange").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class InventoryFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")

    def test_intake_creates_item_and_history(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "serial_number": "847291",
                "item_type": "MOTOR",
                "status": "PARTS",
                "notes": "Intake from dock",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(serial_number="847291")
        self.assertEqual(item.display_name, "847291.IndustrialMotor.ForParts")
        self.assertEqual(item.status_history.count(), 1)
        self.assertEqual(item.status_history.first().changed_by, self.user)

    def test_intake_saves_photos(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "serial_number": "PHOTO1",
                "item_type": "MOTOR",
                "status": "REVIEW",
                "photos": [jpeg_file("barcode.jpg"), jpeg_file("body.jpg")],
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(serial_number="PHOTO1")
        self.assertEqual(item.photos.count(), 2)

    def test_detail_accepts_more_photos(self):
        item = Item.objects.create(serial_number="PHOTO2", item_type="PUMP", status="ACTIVE")
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {
                "intent": "photos",
                "kind": "BARCODE",
                "photos": jpeg_file("label.jpg"),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.photos.count(), 1)
        self.assertEqual(item.photos.first().kind, "BARCODE")

    def test_list_filters_by_status(self):
        Item.objects.create(serial_number="A1", item_type="PUMP", status="ACTIVE")
        Item.objects.create(serial_number="B2", item_type="VALVE", status="REVIEW")
        response = self.client.get(reverse("inventory:item_list"), {"status": "ACTIVE"})
        self.assertContains(response, "A1")
        self.assertNotContains(response, "B2")

    def test_status_change_writes_history(self):
        item = Item.objects.create(serial_number="C3", item_type="MOTOR", status="REVIEW")
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"status": "ACTIVE", "notes": "Ready for service"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "ACTIVE")
        self.assertEqual(item.status_history.count(), 2)

    def test_unauthenticated_users_are_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("inventory:item_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
