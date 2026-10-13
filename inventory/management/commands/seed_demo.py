"""Seed (or reset-and-reseed) a standalone DEMO instance with deterministic,
entirely fictional inventory/order data for the Superhuman Go buildathon
agent (brief Section 5).

Usage:
    python manage.py seed_demo             # seed only if empty
    python manage.py seed_demo --reset      # wipe and reseed (needs the
                                             # confirmation below too)

Hard safety constraint (brief Section 3): this agent -- and this command --
must NEVER run against the live IMS. --reset is destructive (it deletes
every Item and Order row), so on top of requiring the flag, it also refuses
to run unless the DEMO_SEED_CONFIRM=yes environment variable is set. That
env var is something you set only in the demo stack's own .env, specifically
so a --reset typed against the wrong environment by habit or muscle memory
fails closed instead of wiping real data.

Data note: none of the model names below resemble anything in
seed_epcom_models.py's real Epcom catalog -- the original brief's own
example model names ("XE43-GEN3", "PL12DC3ABK") turned out to be a real
Epcom SKU ("PL12DC3ABK", Access Control) and a near-match of one
("XE43D-GEN3", Video Surveillance), so neither is used here. Everything
below is invented for this seed script.
"""

import os
import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Category,
    Item,
    Location,
    Order,
    OrderLine,
    Subcategory,
    SubSubcategory,
)

# Fixed seed -- every run (given the same existing category/location rows)
# lays out quantities and locations identically, so the demo looks the same
# take after take (brief Section 5: "Deterministic (fixed random seed)").
RNG_SEED = 20261008

STATUSES = ["ACTIVE", "PARTS", "REPAIR", "REVIEW"]
# Realistic-ish mix: mostly Active, with the other three statuses getting
# progressively less common -- not a spec requirement, just keeps the bulk
# filler items from looking uniformly random.
STATUS_WEIGHTS = [70, 15, 10, 5]

CATEGORY_TREE = {
    "Radio Communication": {
        "Portable & Mobile Radios": ["Digital Portable Radios", "Mobile Radios"],
        "Antennas & Cable": ["Antennas", "Coax Cable"],
        "Power": ["Batteries", "Chargers"],
    },
    "Video Surveillance": {
        "CCTV": ["Dome Cameras", "NVRs"],
    },
    "IoT & GPS": {
        "Tracking": ["GPS Trackers"],
    },
    "Mounting & Hardware": {
        "Mounting Hardware": ["Pole Mounts", "Wall Brackets"],
    },
}

LOCATIONS = [
    {"building": "Warehouse A", "aisle": "1", "shelf": "1", "bin": "A"},
    {"building": "Warehouse A", "aisle": "1", "shelf": "2", "bin": "B"},
    {"building": "Warehouse A", "aisle": "2", "shelf": "1", "bin": "A"},
    {"building": "Warehouse B", "aisle": "1", "shelf": "1", "bin": "A"},
    {"building": "Warehouse B", "aisle": "3", "shelf": "2", "bin": "C"},
    {"building": "Repair Bench", "aisle": "", "shelf": "", "bin": ""},
]

CLIENTS = [
    ("Rio Grande Logistics", "orders@riograndelogistics.example"),
    ("Laredo Test Fleet Co.", "dispatch@laredotestfleet.example"),
    ("Del Rio Security Partners", "procurement@delriosecurity.example"),
]


class Command(BaseCommand):
    help = (
        "Seed a standalone DEMO instance with deterministic, entirely "
        "fictional inventory/order data for the buildathon agent. Refuses "
        "to --reset without DEMO_SEED_CONFIRM=yes set (brief Section 3: "
        "never run this against the live IMS)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing Items/Orders (and everything that cascades from "
            "them) before reseeding. Requires DEMO_SEED_CONFIRM=yes in the environment.",
        )

    def handle(self, *args, **options):
        reset = options["reset"]
        existing_items = Item.objects.count()
        existing_orders = Order.objects.count()

        if reset:
            if not _confirm_env_set():
                raise CommandError(
                    "--reset refused: set DEMO_SEED_CONFIRM=yes in this instance's "
                    "environment first. This is a deliberate two-factor guard -- "
                    "--reset deletes every Item and Order row, and this must never "
                    "run against the live IMS (brief Section 3)."
                )
            self.stdout.write(
                self.style.WARNING(
                    f"--reset: deleting {existing_items} existing item(s) and "
                    f"{existing_orders} existing order(s)..."
                )
            )
            with transaction.atomic():
                Order.objects.all().delete()  # cascades to OrderLine
                Item.objects.all().delete()  # cascades to StatusHistory, ItemPhoto
        elif existing_items or existing_orders:
            raise CommandError(
                f"Found {existing_items} existing item(s) and {existing_orders} "
                "existing order(s) -- refusing to seed on top of them. Re-run with "
                "--reset to wipe and reseed, or start from a genuinely empty database."
            )

        rng = random.Random(RNG_SEED)

        with transaction.atomic():
            subsubcats = self._ensure_categories()
            locations = self._ensure_locations()
            items_by_model = self._seed_items(subsubcats, locations, rng)
            order_count = self._seed_orders(items_by_model)

        total_items = Item.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {total_items} item lot(s) across {len(items_by_model)} model(s) "
                f"and {order_count} order(s)."
            )
        )

    # -- categories -----------------------------------------------------

    def _ensure_categories(self):
        """get_or_create all the way down, so this is safe to call whether
        or not this command has ever run before, and never collides with
        the real load_categories.py catalog (different category names)."""
        subsubcats = {}
        for cat_name, subcats in CATEGORY_TREE.items():
            category, _ = Category.objects.get_or_create(name=cat_name)
            for subcat_name, ssc_names in subcats.items():
                subcategory, _ = Subcategory.objects.get_or_create(
                    category=category, name=subcat_name
                )
                for ssc_name in ssc_names:
                    ssc, _ = SubSubcategory.objects.get_or_create(
                        subcategory=subcategory, name=ssc_name
                    )
                    subsubcats[ssc_name] = ssc
        return subsubcats

    def _ensure_locations(self):
        locations = []
        for spec in LOCATIONS:
            location, _ = Location.objects.get_or_create(**spec)
            locations.append(location)
        return locations

    # -- items ------------------------------------------------------------

    def _seed_items(self, subsubcats, locations, rng):
        """Returns {model_name: [Item, ...]} for every lot created, so
        _seed_orders can reference real items by model name afterward."""
        items_by_model: dict[str, list[Item]] = {}

        def make_lot(model_name, ssc_name, status, qty, location):
            item = Item.objects.create(
                model_name=model_name,
                subsubcategory=subsubcats[ssc_name],
                status=status,
                quantity=qty,
                location=location,
            )
            items_by_model.setdefault(model_name, []).append(item)
            return item

        def random_filler_lots(model_name, ssc_name, count):
            for _ in range(count):
                status = rng.choices(STATUSES, weights=STATUS_WEIGHTS, k=1)[0]
                qty = rng.randint(1, 12)
                location = rng.choice(locations)
                make_lot(model_name, ssc_name, status, qty, location)

        # -- Deliberately engineered demo moments (brief Section 5) --

        # One model split across lots in every status -- the headline
        # get_stock breakdown demo ("9 total: 5 Active, 2 For Parts, ...").
        make_lot("RDX-7700", "Digital Portable Radios", "ACTIVE", 5, locations[0])
        make_lot("RDX-7700", "Digital Portable Radios", "PARTS", 2, locations[1])
        make_lot("RDX-7700", "Digital Portable Radios", "REPAIR", 1, locations[5])
        make_lot("RDX-7700", "Digital Portable Radios", "REVIEW", 1, locations[2])

        # A similarly-named model, to test disambiguation against RDX-7700.
        make_lot("RDX-7700S", "Digital Portable Radios", "ACTIVE", 3, locations[3])

        # Zero on-hand, but (via _seed_orders below) has shipment history --
        # the get_stock(0 total) / get_order_history pairing demo moment.
        make_lot("ANT-CB27", "Antennas", "ACTIVE", 0, locations[0])

        # A second similarly-named pair (camera + its wifi variant).
        make_lot("CAM-DM812", "Dome Cameras", "ACTIVE", 6, locations[0])
        make_lot("CAM-DM812", "Dome Cameras", "PARTS", 1, locations[5])
        make_lot("CAM-DM812W", "Dome Cameras", "ACTIVE", 4, locations[3])
        make_lot("CAM-DM812W", "Dome Cameras", "REVIEW", 2, locations[4])

        # -- Bulk filler across the remaining product families --
        random_filler_lots("RDX-M450", "Mobile Radios", 3)
        random_filler_lots("CBL-RG58-100", "Coax Cable", 4)
        random_filler_lots("CBL-RG8X-50", "Coax Cable", 3)
        random_filler_lots("BAT-LI7P", "Batteries", 4)
        random_filler_lots("CHG-DK6", "Chargers", 3)
        random_filler_lots("NVR-16CH", "NVRs", 3)
        random_filler_lots("NVR-8CH", "NVRs", 3)
        random_filler_lots("GPS-TRK4", "GPS Trackers", 4)
        random_filler_lots("MNT-POLE2", "Pole Mounts", 3)
        random_filler_lots("MNT-WALL1", "Wall Brackets", 3)

        return items_by_model

    # -- orders -----------------------------------------------------------

    def _seed_orders(self, items_by_model):
        """2-3+ historical orders, including one well outside the default
        90-day get_order_history window (brief Section 5 + 6) so the demo
        can show days=90 (default) vs. days=0/all actually differing."""

        def backdated_order(client_name, client_email, days_ago, lines):
            order = Order.objects.create(client_name=client_name, client_email=client_email)
            Order.objects.filter(pk=order.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago)
            )
            for item, qty in lines:
                OrderLine.objects.create(order=order, item=item, quantity_shipped=qty)
            return order

        rdx_lot = items_by_model["RDX-7700"][0]  # the 5-qty Active lot
        cam_lot = items_by_model["CAM-DM812"][0]  # the 6-qty Active lot
        ant_lot = items_by_model["ANT-CB27"][0]  # the 0-qty lot

        backdated_order(
            *CLIENTS[0], days_ago=10, lines=[(rdx_lot, 3)]
        )
        backdated_order(
            *CLIENTS[1], days_ago=45, lines=[(cam_lot, 2), (rdx_lot, 1)]
        )
        # Outside the default 90-day window on purpose -- only shows up
        # with days=0/"all", exercising the exact default-vs-all-time split
        # OrderViewSet.get_queryset() implements.
        backdated_order(
            *CLIENTS[2], days_ago=120, lines=[(ant_lot, 4)]
        )

        return Order.objects.count()


def _confirm_env_set():
    return os.environ.get("DEMO_SEED_CONFIRM") == "yes"
