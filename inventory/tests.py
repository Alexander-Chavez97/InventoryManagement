from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from .forms import ItemFilterForm, ScannerIntakeForm
from .models import Category, Item, Location, Subcategory, SubSubcategory


def jpeg_file(name="shot.jpg"):
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "orange").save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


def make_category_chain(category="Radio Communication", subcategory="Commercial Radios & Systems", subsubcategory="Digital Portable Radios"):
    """A minimal Category/Subcategory/SubSubcategory chain for intake tests.

    Not dependent on the `load_categories` management command or the catalog
    .txt files -- kept self-contained so these tests stay fast and don't
    care what the real catalog currently contains.
    """
    cat = Category.objects.create(name=category)
    sub = Subcategory.objects.create(category=cat, name=subcategory)
    ssc = SubSubcategory.objects.create(subcategory=sub, name=subsubcategory)
    return cat, sub, ssc


class InventoryFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.category, self.subcategory, self.subsubcategory = make_category_chain()

    def test_intake_creates_item_and_history(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "serial_number": "847291",
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "PARTS",
                "notes": "Intake from dock",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(serial_number="847291")
        self.assertEqual(item.display_name, "847291.RadioCommunication.ForParts")
        self.assertEqual(item.status_history.count(), 1)
        self.assertEqual(item.status_history.first().changed_by, self.user)

    def test_intake_saves_photos(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "serial_number": "PHOTO1",
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "REVIEW",
                "photos": [jpeg_file("barcode.jpg"), jpeg_file("body.jpg")],
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(serial_number="PHOTO1")
        self.assertEqual(item.photos.count(), 2)

    def test_detail_accepts_more_photos(self):
        item = Item.objects.create(serial_number="PHOTO2", item_type="VEHICLE", status="ACTIVE")
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
        Item.objects.create(serial_number="A1", item_type="VEHICLE", status="ACTIVE")
        Item.objects.create(serial_number="B2", item_type="RADIO", status="REVIEW")
        response = self.client.get(reverse("inventory:item_list"), {"status": "ACTIVE"})
        self.assertContains(response, "A1")
        self.assertNotContains(response, "B2")

    def test_status_change_writes_history(self):
        item = Item.objects.create(serial_number="C3", item_type="RADIO", status="REVIEW")
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"quantity": 1, "status": "ACTIVE", "notes": "Ready for service"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "ACTIVE")
        self.assertEqual(item.status_history.count(), 2)

    def test_quantity_defaults_to_one_and_is_editable_from_item_detail(self):
        item = Item.objects.create(serial_number="C4", item_type="RADIO", status="REVIEW")
        self.assertEqual(item.quantity, 1)
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"quantity": 7, "status": "REVIEW", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 7)

    def test_quantity_cannot_be_negative(self):
        item = Item.objects.create(serial_number="C5", item_type="RADIO", status="REVIEW")
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"quantity": -3, "status": "REVIEW", "notes": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "greater than or equal to 0")
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)

    def test_unauthenticated_users_are_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("inventory:item_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class ItemModelTests(TestCase):
    def test_display_name_strips_spaces_from_choice_labels(self):
        item = Item.objects.create(serial_number="D1", item_type="VEHICLE", status="REPAIR")
        self.assertEqual(item.display_name, "D1.Vehicle.PendingRepair")

    def test_display_name_prefers_the_new_category_over_legacy_item_type(self):
        _, _, ssc = make_category_chain(
            category="Video Surveillance", subcategory="IP Cameras", subsubcategory="Dome"
        )
        item = Item.objects.create(serial_number="D0", subsubcategory=ssc, status="ACTIVE")
        self.assertEqual(item.display_name, "D0.VideoSurveillance.Active")
        self.assertEqual(item.category_path, "Video Surveillance → IP Cameras → Dome")

    def test_creating_an_item_writes_one_history_entry(self):
        item = Item.objects.create(serial_number="D2", item_type="CAMERA", status="REVIEW")
        self.assertEqual(item.status_history.count(), 1)
        entry = item.status_history.first()
        self.assertEqual(entry.old_status, "")
        self.assertEqual(entry.new_status, "REVIEW")

    def test_saving_without_a_status_change_does_not_add_history(self):
        item = Item.objects.create(serial_number="D3", item_type="CAMERA", status="ACTIVE")
        item.notes = "Recalibrated the lens"
        item.save()
        self.assertEqual(item.status_history.count(), 1)

    def test_changing_status_appends_history_without_removing_old_entries(self):
        item = Item.objects.create(serial_number="D4", item_type="RADIO", status="REVIEW")
        item.status = "ACTIVE"
        item.save()
        item.status = "PARTS"
        item.save()
        self.assertEqual(item.status_history.count(), 3)
        transitions = list(
            item.status_history.order_by("changed_at").values_list("old_status", "new_status")
        )
        self.assertEqual(
            transitions,
            [("", "REVIEW"), ("REVIEW", "ACTIVE"), ("ACTIVE", "PARTS")],
        )

    def test_serial_number_must_be_unique(self):
        Item.objects.create(serial_number="DUP", item_type="RADIO", status="ACTIVE")
        with self.assertRaises(IntegrityError):
            Item.objects.create(serial_number="DUP", item_type="CAMERA", status="ACTIVE")

    def test_location_str_skips_blank_parts(self):
        location = Location.objects.create(building="Warehouse A", aisle="", shelf="3", bin="")
        self.assertEqual(str(location), "Warehouse A - 3")


class CategoryHierarchyTests(TestCase):
    def test_subcategory_is_unique_per_category_not_globally(self):
        cat_a, _, _ = make_category_chain(category="Access Control", subcategory="Accessories", subsubcategory="Hinges")
        cat_b = Category.objects.create(name="Intrusion Systems")
        # "Accessories" under a different category is not a collision.
        Subcategory.objects.create(category=cat_b, name="Accessories")
        self.assertEqual(Subcategory.objects.filter(name="Accessories").count(), 2)

    def test_subcategory_name_must_be_unique_within_its_category(self):
        cat, _, _ = make_category_chain()
        Subcategory.objects.create(category=cat, name="Antennas")
        with self.assertRaises(IntegrityError):
            Subcategory.objects.create(category=cat, name="Antennas")


class FormTests(TestCase):
    def setUp(self):
        self.category, self.subcategory, self.subsubcategory = make_category_chain()

    def test_scanner_intake_form_strips_serial_whitespace(self):
        form = ScannerIntakeForm(
            data={
                "serial_number": "  123456  ",
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["serial_number"], "123456")

    def test_scanner_intake_form_rejects_a_subcategory_from_another_category(self):
        other_category = Category.objects.create(name="Networking")
        other_subcategory = Subcategory.objects.create(category=other_category, name="Antennas")
        form = ScannerIntakeForm(
            data={
                "serial_number": "999999",
                "category": self.category.pk,
                "subcategory": other_subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("subcategory", form.errors)

    def test_item_filter_form_allows_blank_filters(self):
        form = ItemFilterForm(data={})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["status"], "")


class PaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        for i in range(30):
            Item.objects.create(serial_number=f"P{i:03d}", item_type="RADIO", status="ACTIVE")

    def test_first_page_shows_25_items_with_a_next_link(self):
        response = self.client.get(reverse("inventory:item_list"))
        self.assertEqual(len(response.context["page_obj"]), 25)
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "Next")
        self.assertNotContains(response, "Prev")

    def test_second_page_shows_the_remainder_with_a_prev_link(self):
        response = self.client.get(reverse("inventory:item_list"), {"page": 2})
        self.assertEqual(len(response.context["page_obj"]), 5)
        self.assertContains(response, "Prev")
        self.assertNotContains(response, "Next")

    def test_pagination_controls_are_hidden_for_a_single_page(self):
        Item.objects.all().delete()
        Item.objects.create(serial_number="ONLY1", item_type="RADIO", status="ACTIVE")
        response = self.client.get(reverse("inventory:item_list"))
        self.assertNotContains(response, "pagination")


class QuickStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.item = Item.objects.create(serial_number="Q1", item_type="RADIO", status="REVIEW")

    def test_valid_status_change_updates_the_item(self):
        response = self.client.post(
            reverse("inventory:quick_status", args=[self.item.pk]),
            {"status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "ACTIVE")

    def test_invalid_status_is_rejected(self):
        response = self.client.post(
            reverse("inventory:quick_status", args=[self.item.pk]),
            {"status": "NOT_A_REAL_STATUS"},
        )
        self.assertEqual(response.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "REVIEW")

    def test_get_requests_are_rejected(self):
        response = self.client.get(reverse("inventory:quick_status", args=[self.item.pk]))
        self.assertEqual(response.status_code, 405)

    def test_htmx_request_returns_the_paginated_partial(self):
        response = self.client.post(
            reverse("inventory:quick_status", args=[self.item.pk]),
            {"status": "ACTIVE"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "inventory/partials/item_rows.html")

    def test_anonymous_users_cannot_change_status(self):
        self.client.logout()
        response = self.client.post(
            reverse("inventory:quick_status", args=[self.item.pk]),
            {"status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "REVIEW")


class CascadingCategoryViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.category, self.subcategory, self.subsubcategory = make_category_chain()
        self.other_subcategory = Subcategory.objects.create(category=self.category, name="Antennas")

    def test_subcategory_options_returns_only_that_categorys_subcategories(self):
        response = self.client.get(
            reverse("inventory:subcategory_options"), {"category": self.category.pk}
        )
        # "&" is HTML-escaped by the template, so check the rendered form.
        self.assertContains(response, "Commercial Radios &amp; Systems")
        self.assertContains(response, self.other_subcategory.name)

    def test_subcategory_options_is_empty_without_a_category(self):
        response = self.client.get(reverse("inventory:subcategory_options"))
        self.assertNotContains(response, self.subcategory.name)

    def test_subsubcategory_options_returns_only_that_subcategorys_leaves(self):
        response = self.client.get(
            reverse("inventory:subsubcategory_options"), {"subcategory": self.subcategory.pk}
        )
        self.assertContains(response, self.subsubcategory.name)


class StaffPermissionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("boss", password="test-pass-123", is_staff=True)
        self.regular = User.objects.create_user("alex", password="test-pass-123")

    def test_staff_user_can_create_a_new_staff_account(self):
        self.client.login(username="boss", password="test-pass-123")
        response = self.client.post(
            reverse("inventory:create_staff"),
            {
                "username": "newhire",
                "password1": "a-strong-passw0rd!",
                "password2": "a-strong-passw0rd!",
            },
        )
        self.assertEqual(response.status_code, 302)
        new_user = User.objects.get(username="newhire")
        self.assertFalse(new_user.is_staff)
        self.assertFalse(new_user.is_superuser)

    def test_creating_a_staff_account_ignores_an_is_staff_override_attempt(self):
        self.client.login(username="boss", password="test-pass-123")
        self.client.post(
            reverse("inventory:create_staff"),
            {
                "username": "sneaky",
                "password1": "a-strong-passw0rd!",
                "password2": "a-strong-passw0rd!",
                "is_staff": "on",
                "is_superuser": "on",
            },
        )
        new_user = User.objects.get(username="sneaky")
        self.assertFalse(new_user.is_staff)
        self.assertFalse(new_user.is_superuser)

    def test_regular_users_are_redirected_away_from_staff_creation(self):
        self.client.login(username="alex", password="test-pass-123")
        response = self.client.get(reverse("inventory:create_staff"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username="newhire").exists())

    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(reverse("inventory:create_staff"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)


class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client = APIClient()
        self.active = Item.objects.create(serial_number="API1", item_type="CAMERA", status="ACTIVE")
        Item.objects.create(serial_number="API2", item_type="RADIO", status="REVIEW")

    def test_anonymous_requests_are_rejected(self):
        response = self.client.get("/api/items/")
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_users_can_list_items(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/items/")
        self.assertEqual(response.status_code, 200)
        serials = {row["serial_number"] for row in response.data}
        self.assertEqual(serials, {"API1", "API2"})

    def test_filtering_by_status(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/items/", {"status": "ACTIVE"})
        serials = [row["serial_number"] for row in response.data]
        self.assertEqual(serials, ["API1"])

    def test_detail_endpoint_includes_display_fields(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(f"/api/items/{self.active.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status_display"], "Active")
        self.assertEqual(response.data["item_type_display"], "Camera")


@override_settings(AXES_ENABLED=True)
class LoginLockoutTests(TestCase):
    """Verifies django-axes actually blocks repeated bad logins.

    AXES_ENABLED is off by default during `manage.py test` (see
    main/settings.py) so the rest of the suite isn't affected by
    login attempts made elsewhere; this test turns it back on to
    check the brute-force protection itself.
    """

    def setUp(self):
        from axes.utils import reset as axes_reset

        User.objects.create_user("alex", password="correct-horse-battery")
        axes_reset()

    def tearDown(self):
        from axes.utils import reset as axes_reset

        axes_reset()

    def test_repeated_bad_passwords_lock_out_further_attempts(self):
        for _ in range(5):
            self.client.post(
                reverse("login"),
                {"username": "alex", "password": "wrong-password"},
            )
        response = self.client.post(
            reverse("login"),
            {"username": "alex", "password": "correct-horse-battery"},
        )
        # A locked-out attempt must not succeed, even with the right password.
        self.assertNotEqual(response.status_code, 302)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
