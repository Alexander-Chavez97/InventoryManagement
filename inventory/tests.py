from io import BytesIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from .forms import ItemFilterForm, ScannerIntakeForm
from .models import (
    Category,
    Item,
    ItemPhoto,
    Location,
    MAX_PHOTO_DIMENSION,
    ModelCatalog,
    Order,
    Subcategory,
    SubSubcategory,
)


def jpeg_file(name="shot.jpg", size=(20, 20)):
    buffer = BytesIO()
    Image.new("RGB", size, "orange").save(buffer, format="JPEG")
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
                "model_name": "847291",
                "quantity": 1,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "PARTS",
                "notes": "Intake from dock",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(model_name="847291")
        self.assertEqual(item.display_name, "847291.RadioCommunication.ForParts")
        self.assertEqual(item.status_history.count(), 1)
        self.assertEqual(item.status_history.first().changed_by, self.user)

    def test_intake_sets_quantity(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "model_name": "QTY1",
                "quantity": 5,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(model_name="QTY1")
        self.assertEqual(item.quantity, 5)

    def test_intake_saves_photos(self):
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "model_name": "PHOTO1",
                "quantity": 1,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "REVIEW",
                "photos": [jpeg_file("barcode.jpg"), jpeg_file("body.jpg")],
            },
        )
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(model_name="PHOTO1")
        self.assertEqual(item.photos.count(), 2)

    def test_detail_accepts_more_photos(self):
        item = Item.objects.create(model_name="PHOTO2", item_type="VEHICLE", status="ACTIVE")
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
        Item.objects.create(model_name="A1", item_type="VEHICLE", status="ACTIVE")
        Item.objects.create(model_name="B2", item_type="RADIO", status="REVIEW")
        response = self.client.get(reverse("inventory:item_list"), {"status": "ACTIVE"})
        self.assertContains(response, "A1")
        self.assertNotContains(response, "B2")

    def test_list_hides_out_of_stock_items_by_default(self):
        Item.objects.create(model_name="INSTOCK1", item_type="VEHICLE", status="ACTIVE", quantity=3)
        Item.objects.create(model_name="EMPTY1", item_type="VEHICLE", status="ACTIVE", quantity=0)
        response = self.client.get(reverse("inventory:item_list"))
        self.assertContains(response, "INSTOCK1")
        self.assertNotContains(response, "EMPTY1")

    def test_list_shows_out_of_stock_items_when_requested(self):
        Item.objects.create(model_name="EMPTY2", item_type="VEHICLE", status="ACTIVE", quantity=0)
        response = self.client.get(reverse("inventory:item_list"), {"include_out_of_stock": "on"})
        self.assertContains(response, "EMPTY2")

    def test_total_count_follows_out_of_stock_toggle(self):
        Item.objects.create(model_name="INSTOCK2", item_type="VEHICLE", status="ACTIVE", quantity=3)
        Item.objects.create(model_name="EMPTY3", item_type="VEHICLE", status="ACTIVE", quantity=0)
        response = self.client.get(reverse("inventory:item_list"))
        self.assertEqual(response.context["counts"]["total"], 1)
        response = self.client.get(reverse("inventory:item_list"), {"include_out_of_stock": "on"})
        self.assertEqual(response.context["counts"]["total"], 2)

    def test_status_change_writes_history(self):
        item = Item.objects.create(model_name="C3", item_type="RADIO", status="REVIEW")
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"quantity": 1, "status": "ACTIVE", "notes": "Ready for service"},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "ACTIVE")
        self.assertEqual(item.status_history.count(), 2)

    def test_quantity_defaults_to_one_and_is_editable_from_item_detail(self):
        item = Item.objects.create(model_name="C4", item_type="RADIO", status="REVIEW")
        self.assertEqual(item.quantity, 1)
        response = self.client.post(
            reverse("inventory:item_detail", args=[item.pk]),
            {"quantity": 7, "status": "REVIEW", "notes": ""},
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 7)

    def test_quantity_cannot_be_negative(self):
        item = Item.objects.create(model_name="C5", item_type="RADIO", status="REVIEW")
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
        item = Item.objects.create(model_name="D1", item_type="VEHICLE", status="REPAIR")
        self.assertEqual(item.display_name, "D1.Vehicle.PendingRepair")

    def test_display_name_prefers_the_new_category_over_legacy_item_type(self):
        _, _, ssc = make_category_chain(
            category="Video Surveillance", subcategory="IP Cameras", subsubcategory="Dome"
        )
        item = Item.objects.create(model_name="D0", subsubcategory=ssc, status="ACTIVE")
        self.assertEqual(item.display_name, "D0.VideoSurveillance.Active")
        self.assertEqual(item.category_path, "Video Surveillance → IP Cameras → Dome")

    def test_creating_an_item_writes_one_history_entry(self):
        item = Item.objects.create(model_name="D2", item_type="CAMERA", status="REVIEW")
        self.assertEqual(item.status_history.count(), 1)
        entry = item.status_history.first()
        self.assertEqual(entry.old_status, "")
        self.assertEqual(entry.new_status, "REVIEW")

    def test_saving_without_a_status_change_does_not_add_history(self):
        item = Item.objects.create(model_name="D3", item_type="CAMERA", status="ACTIVE")
        item.notes = "Recalibrated the lens"
        item.save()
        self.assertEqual(item.status_history.count(), 1)

    def test_changing_status_appends_history_without_removing_old_entries(self):
        item = Item.objects.create(model_name="D4", item_type="RADIO", status="REVIEW")
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

    def test_multiple_items_can_share_a_model_name(self):
        # Model name is a product identifier, not a per-unit serial -- many
        # physical units legitimately share one (quantity tracks how many).
        Item.objects.create(model_name="XE43-GEN3", item_type="RADIO", status="ACTIVE")
        Item.objects.create(model_name="XE43-GEN3", item_type="CAMERA", status="ACTIVE")
        self.assertEqual(Item.objects.filter(model_name="XE43-GEN3").count(), 2)

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
                "model_name": "  123456  ",
                "quantity": 1,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["model_name"], "123456")

    def test_scanner_intake_form_rejects_a_subcategory_from_another_category(self):
        other_category = Category.objects.create(name="Networking")
        other_subcategory = Subcategory.objects.create(category=other_category, name="Antennas")
        form = ScannerIntakeForm(
            data={
                "model_name": "999999",
                "quantity": 1,
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
            Item.objects.create(model_name=f"P{i:03d}", item_type="RADIO", status="ACTIVE")

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
        Item.objects.create(model_name="ONLY1", item_type="RADIO", status="ACTIVE")
        response = self.client.get(reverse("inventory:item_list"))
        self.assertNotContains(response, "pagination")


class QuickStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.item = Item.objects.create(model_name="Q1", item_type="RADIO", status="REVIEW")

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
        self.active = Item.objects.create(model_name="API1", item_type="CAMERA", status="ACTIVE")
        Item.objects.create(model_name="API2", item_type="RADIO", status="REVIEW")

    def test_anonymous_requests_are_rejected(self):
        response = self.client.get("/api/items/")
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_users_can_list_items(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/items/")
        self.assertEqual(response.status_code, 200)
        # Paginated response ({"count", "next", "previous", "results"}), not
        # a bare list -- see DEFAULT_PAGINATION_CLASS in REST_FRAMEWORK.
        serials = {row["model_name"] for row in response.data["results"]}
        self.assertEqual(serials, {"API1", "API2"})

    def test_filtering_by_status(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/items/", {"status": "ACTIVE"})
        serials = [row["model_name"] for row in response.data["results"]]
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


def _order_formset_data(lines, total=8):
    """Build the `line-...` formset POST fields for a list of (item, qty)
    tuples, padding the rest of the formset out to `total` blank rows."""
    data = {
        "line-TOTAL_FORMS": str(total),
        "line-INITIAL_FORMS": "0",
        "line-MIN_NUM_FORMS": "0",
        "line-MAX_NUM_FORMS": "1000",
    }
    for i in range(total):
        if i < len(lines):
            item, qty = lines[i]
            data[f"line-{i}-item"] = str(item.pk)
            data[f"line-{i}-quantity_shipped"] = str(qty)
        else:
            data[f"line-{i}-item"] = ""
            data[f"line-{i}-quantity_shipped"] = ""
    return data


class OrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        cat, sub, ssc = make_category_chain()
        self.item = Item.objects.create(
            model_name="SHIP1", subsubcategory=ssc, status="ACTIVE", quantity=10
        )

    def _header_data(self, **overrides):
        data = {
            "client_name": "Acme Corp",
            "client_email": "acme@example.com",
            "job_reference": "PO-100",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_preview_does_not_touch_inventory_or_send_email(self):
        data = {**self._header_data(), **_order_formset_data([(self.item, 3)])}
        response = self.client.post(reverse("inventory:order_new"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm this order?")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_decrements_inventory_creates_order_and_emails_client(self):
        data = {
            **self._header_data(),
            **_order_formset_data([(self.item, 3)]),
            "confirm": "1",
        }
        response = self.client.post(reverse("inventory:order_new"), data)
        self.assertEqual(response.status_code, 302)

        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 7)

        order = Order.objects.get()
        self.assertEqual(order.client_name, "Acme Corp")
        self.assertEqual(order.client_email, "acme@example.com")
        self.assertEqual(order.job_reference, "PO-100")
        self.assertEqual(order.created_by, self.user)
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.lines.get().quantity_shipped, 3)
        self.assertIsNotNone(order.email_sent_at)

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["acme@example.com"])
        self.assertIn("SHIP1", sent.body)
        self.assertIn("3", sent.body)

    def test_cannot_ship_more_than_on_hand(self):
        data = {
            **self._header_data(),
            **_order_formset_data([(self.item, 11)]),  # only 10 on hand
            "confirm": "1",
        }
        response = self.client.post(reverse("inventory:order_new"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only 10 on hand")
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 10)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_same_item_on_two_lines_is_rejected(self):
        data = {
            **self._header_data(),
            **_order_formset_data([(self.item, 2), (self.item, 1)]),
        }
        response = self.client.post(reverse("inventory:order_new"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already on another line")
        self.assertEqual(Order.objects.count(), 0)

    def test_order_requires_at_least_one_line(self):
        data = {**self._header_data(), **_order_formset_data([])}
        response = self.client.post(reverse("inventory:order_new"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add at least one item")

    def test_item_detail_shows_order_history(self):
        data = {
            **self._header_data(),
            **_order_formset_data([(self.item, 4)]),
            "confirm": "1",
        }
        self.client.post(reverse("inventory:order_new"), data)
        response = self.client.get(reverse("inventory:item_detail", args=[self.item.pk]))
        self.assertContains(response, "4 sent to")
        self.assertContains(response, "Acme")


class ModelCatalogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.category, self.subcategory, self.subsubcategory = make_category_chain()

    def _intake(self, model_name):
        return self.client.post(
            reverse("inventory:intake"),
            {
                "model_name": model_name,
                "quantity": 1,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
            },
        )

    def test_a_name_matching_the_known_format_is_added_and_not_flagged(self):
        self._intake("XQ99-GEN5")
        entry = ModelCatalog.objects.get(name="XQ99-GEN5")
        self.assertIsNotNone(entry.matched_pattern)
        self.assertFalse(entry.needs_review)

    def test_a_name_matching_no_known_format_is_still_recorded_but_flagged(self):
        response = self._intake("12345")
        self.assertEqual(response.status_code, 302)
        entry = ModelCatalog.objects.get(name="12345")
        self.assertIsNone(entry.matched_pattern)
        self.assertTrue(entry.needs_review)
        # The item itself was still recorded -- this is a warning, not a block.
        self.assertTrue(Item.objects.filter(model_name="12345").exists())

    def test_reusing_an_existing_model_name_does_not_duplicate_the_catalog_entry(self):
        # Same model, category, status and location each time -- this also
        # merges into a single Item lot (see IntakeMergeTests), so there's
        # exactly one Item row as well as one catalog entry.
        self._intake("xb41h")
        self._intake("XB41H")
        self.assertEqual(ModelCatalog.objects.filter(name__iexact="XB41H").count(), 1)
        self.assertEqual(Item.objects.filter(model_name="XB41H").count(), 1)
        self.assertEqual(Item.objects.get(model_name="XB41H").quantity, 2)

    def test_model_name_is_normalized_to_uppercase(self):
        self._intake("xe43-gen3")
        self.assertTrue(Item.objects.filter(model_name="XE43-GEN3").exists())


class IntakeMergeTests(TestCase):
    """Intaking the same model/category/status/location twice should add to
    the existing lot rather than creating a duplicate row; anything that
    differs (status, in particular) should stay a separate lot."""

    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        self.category, self.subcategory, self.subsubcategory = make_category_chain()

    def _intake(self, **overrides):
        data = {
            "model_name": "XB41H",
            "quantity": 2,
            "category": self.category.pk,
            "subcategory": self.subcategory.pk,
            "subsubcategory": self.subsubcategory.pk,
            "status": "ACTIVE",
            "notes": "",
        }
        data.update(overrides)
        return self.client.post(reverse("inventory:intake"), data)

    def test_matching_intake_adds_to_the_existing_lot(self):
        self._intake(quantity=2)
        self._intake(quantity=3)
        self.assertEqual(Item.objects.filter(model_name="XB41H").count(), 1)
        self.assertEqual(Item.objects.get(model_name="XB41H").quantity, 5)

    def test_a_different_status_does_not_merge(self):
        self._intake(quantity=2, status="ACTIVE")
        self._intake(quantity=3, status="REVIEW")
        self.assertEqual(Item.objects.filter(model_name="XB41H").count(), 2)
        self.assertEqual(Item.objects.get(model_name="XB41H", status="ACTIVE").quantity, 2)
        self.assertEqual(Item.objects.get(model_name="XB41H", status="REVIEW").quantity, 3)

    def test_a_different_category_does_not_merge(self):
        other_category, other_sub, other_ssc = make_category_chain(
            category="Video Surveillance", subcategory="IP Cameras", subsubcategory="Dome"
        )
        self._intake(quantity=2)
        self._intake(
            quantity=3,
            category=other_category.pk,
            subcategory=other_sub.pk,
            subsubcategory=other_ssc.pk,
        )
        self.assertEqual(Item.objects.filter(model_name="XB41H").count(), 2)

    def test_merging_backfills_a_missing_price_but_does_not_overwrite_one(self):
        self._intake(quantity=2, price="")
        self._intake(quantity=1, price="19.99")
        item = Item.objects.get(model_name="XB41H")
        self.assertEqual(str(item.price), "19.99")

        self._intake(quantity=1, price="999.00")
        item.refresh_from_db()
        # Price was already set -- the second price entered is ignored.
        self.assertEqual(str(item.price), "19.99")
        self.assertEqual(item.quantity, 4)

    def test_photos_from_a_merged_intake_attach_to_the_existing_item(self):
        self._intake(quantity=2)
        item = Item.objects.get(model_name="XB41H")
        response = self.client.post(
            reverse("inventory:intake"),
            {
                "model_name": "XB41H",
                "quantity": 1,
                "category": self.category.pk,
                "subcategory": self.subcategory.pk,
                "subsubcategory": self.subsubcategory.pk,
                "status": "ACTIVE",
                "notes": "",
                "photos": jpeg_file(),
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.photos.count(), 1)


class SplitStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alex", password="test-pass-123")
        self.client.login(username="alex", password="test-pass-123")
        cat, sub, ssc = make_category_chain()
        self.item = Item.objects.create(
            model_name="XB41H", subsubcategory=ssc, status="ACTIVE", quantity=5
        )

    def test_moving_some_quantity_creates_a_new_lot_and_leaves_the_rest(self):
        response = self.client.post(
            reverse("inventory:item_split_status", args=[self.item.pk]),
            {"quantity": 1, "status": "PARTS"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 4)
        self.assertEqual(self.item.status, "ACTIVE")

        new_lot = Item.objects.get(model_name="XB41H", status="PARTS")
        self.assertEqual(new_lot.quantity, 1)
        self.assertEqual(new_lot.subsubcategory_id, self.item.subsubcategory_id)

    def test_moving_quantity_into_an_existing_matching_lot_merges(self):
        Item.objects.create(
            model_name="XB41H",
            subsubcategory=self.item.subsubcategory,
            status="PARTS",
            quantity=2,
        )
        self.client.post(
            reverse("inventory:item_split_status", args=[self.item.pk]),
            {"quantity": 1, "status": "PARTS"},
        )
        self.assertEqual(Item.objects.filter(model_name="XB41H", status="PARTS").count(), 1)
        self.assertEqual(Item.objects.get(model_name="XB41H", status="PARTS").quantity, 3)

    def test_cannot_move_more_than_on_hand(self):
        response = self.client.post(
            reverse("inventory:item_split_status", args=[self.item.pk]),
            {"quantity": 10, "status": "PARTS"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 5)
        self.assertFalse(Item.objects.filter(model_name="XB41H", status="PARTS").exists())

    def test_cannot_move_to_the_same_status(self):
        response = self.client.post(
            reverse("inventory:item_split_status", args=[self.item.pk]),
            {"quantity": 1, "status": "ACTIVE"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 5)

    def test_moving_all_of_it_leaves_the_source_at_zero(self):
        self.client.post(
            reverse("inventory:item_split_status", args=[self.item.pk]),
            {"quantity": 5, "status": "PARTS"},
        )
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 0)
        self.assertEqual(Item.objects.get(model_name="XB41H", status="PARTS").quantity, 5)


class ItemPhotoResizeTests(TestCase):
    """A big phone-camera photo gets downscaled/recompressed on upload, so
    item pages and the media folder don't keep growing proportional to
    whatever resolution someone's phone happens to shoot at."""

    def setUp(self):
        cat, sub, subsub = make_category_chain()
        self.item = Item.objects.create(
            model_name="XB41H", subsubcategory=subsub, status="ACTIVE", quantity=1
        )

    def test_an_oversized_photo_is_downscaled(self):
        photo = ItemPhoto.objects.create(
            item=self.item,
            image=jpeg_file("big.jpg", size=(3000, 2000)),
        )
        with Image.open(photo.image) as img:
            self.assertLessEqual(max(img.size), MAX_PHOTO_DIMENSION)
        # Aspect ratio is preserved, not squashed to a square.
        with Image.open(photo.image) as img:
            self.assertAlmostEqual(img.size[0] / img.size[1], 3000 / 2000, places=2)

    def test_an_already_small_photo_is_left_at_its_own_size(self):
        photo = ItemPhoto.objects.create(
            item=self.item,
            image=jpeg_file("small.jpg", size=(20, 20)),
        )
        with Image.open(photo.image) as img:
            self.assertEqual(img.size, (20, 20))

    def test_a_file_pillow_cannot_read_is_kept_as_is_rather_than_crashing(self):
        bogus = SimpleUploadedFile("not-a-photo.jpg", b"this is not image data", content_type="image/jpeg")
        photo = ItemPhoto.objects.create(item=self.item, image=bogus)
        photo.image.open()
        self.assertEqual(photo.image.read(), b"this is not image data")
