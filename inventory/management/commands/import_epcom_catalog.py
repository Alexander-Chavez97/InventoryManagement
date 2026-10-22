"""Bulk-import a real EPCOM product export (CSV, from EPCOM's dealer
portal -- see "Menu Level 1/2/3", "Model", "Brand", "Description", "Your
Final Price" columns) into ModelCatalog as vendor reference data for the
intake form's autofill.

This supersedes seed_epcom_models.py, which was a hand-curated sample
gathered before a real export was available (its own docstring says so).
Everything that command seeded is a subset of what this file carries, and
register_model_name() -- used by both -- no-ops on a name already known,
so re-running either command is always safe.

This command only ever writes to ModelCatalog. It never touches Item,
Order, or any other real inventory record -- "autofill reference data",
not "inventory". Safe to re-run against a refreshed export: existing
ModelCatalog rows are updated in place by name, nothing is duplicated.

Three of EPCOM's own top-level "Menu Level 1" groups don't line up 1:1
with our Category/Subcategory/SubSubcategory taxonomy (export quirks on
their end, not ours -- see FORCED_MAPPINGS/CATEGORY_ONLY_MAPPINGS below
for exactly how each is handled, and why).
"""

import csv
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inventory.models import Category, Subcategory, SubSubcategory, register_model_name

# EPCOM's "Fire Detection (test 2)" top-level group is real fire-detection
# product data, just mislabeled on their end (looks like a leftover test
# category name). Our own taxonomy already has this as a subcategory under
# Intrusion Systems (see "Intrusion Systems.txt"), with a single
# "All Products" leaf -- so every row in this group is force-mapped there,
# ignoring that group's own (much finer, but not ours) Menu Level 2/3.
#
# EPCOM's "All Products" top-level group turns out to be their NDAA
# (National Defense Authorization Act compliance) tag for certain Video
# Surveillance products -- Menu Level 2/3 for this group come through as
# ("NDAA", "Video Surveillance"), i.e. inverted relative to every other
# row. We already have "NDAA" as a Video Surveillance subcategory with an
# "All Products" leaf, so this group is force-mapped there too.
FORCED_MAPPINGS = {
    "Fire Detection (test 2)": ("Intrusion Systems", "Fire Detection", "All Products"),
    "All Products": ("Video Surveillance", "NDAA", "All Products"),
}

# EPCOM's "Alarms, Intrusion and Automation" group duplicates our existing
# Intrusion Systems branch under a different, legacy top-level name, but
# its own Menu Level 2/3 values (e.g. "Intrusion Systems - Honeywell" /
# "Expander Modules") don't map cleanly onto any single one of our
# subcategories (Accessories / Alarm Panels & Keypads / Detectors &
# Sensors / Electric Fences / Fire Detection / Smart House - Automation).
# Rather than guess wrong, only the category is forced here -- subcategory
# and sub-subcategory are left unresolved, so intake simply won't offer an
# autofill suggestion for these particular models (same as today).
CATEGORY_ONLY_MAPPINGS = {
    "Alarms, Intrusion and Automation": "Intrusion Systems",
}

# Pure export-formatting quirk -- same category, just spaced differently in
# EPCOM's export than in our own taxonomy.txt files. Checked against the
# real export: this is the only Menu Level 1 value that fails to
# exact-match (case-insensitively) a Category name on its own; every other
# mismatch below Level 1 is EPCOM organizing that branch by a different
# scheme than we do (most notably Radio Communication, which EPCOM splits
# by brand/accessory rather than radio type) -- not guessing those, same
# reasoning as CATEGORY_ONLY_MAPPINGS above.
CATEGORY_NAME_ALIASES = {
    "Energy / Tools": "Energy/Tools",
}


def _clean(value, max_length=None):
    value = (value or "").strip()
    if max_length:
        value = value[:max_length]
    return value


def _parse_price(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


class Command(BaseCommand):
    help = (
        "Import an EPCOM product export (CSV) into ModelCatalog as vendor "
        "reference data for intake autofill. Never touches Item/Order records."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Path to the EPCOM product export CSV.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report what would happen without writing anything.",
        )

    def handle(self, csv_path, dry_run, **options):
        try:
            handle = open(csv_path, newline="", encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CommandError(f"Couldn't open {csv_path}: {exc}")

        category_cache = {}
        subcategory_cache = {}
        subsubcategory_cache = {}

        def get_category(name):
            name = name.strip()
            if name not in category_cache:
                category_cache[name] = Category.objects.filter(name__iexact=name).first()
            return category_cache[name]

        def get_subcategory(category, name):
            key = (category.pk if category else None, name.strip())
            if key not in subcategory_cache:
                subcategory_cache[key] = (
                    Subcategory.objects.filter(category=category, name__iexact=name.strip()).first()
                    if category and name.strip()
                    else None
                )
            return subcategory_cache[key]

        def get_subsubcategory(subcategory, name):
            key = (subcategory.pk if subcategory else None, name.strip())
            if key not in subsubcategory_cache:
                subsubcategory_cache[key] = (
                    SubSubcategory.objects.filter(subcategory=subcategory, name__iexact=name.strip()).first()
                    if subcategory and name.strip()
                    else None
                )
            return subsubcategory_cache[key]

        def resolve_leaf(l1, l2, l3):
            l1 = CATEGORY_NAME_ALIASES.get(l1, l1)
            if l1 in FORCED_MAPPINGS:
                cat_name, sub_name, ssc_name = FORCED_MAPPINGS[l1]
                category = get_category(cat_name)
                subcategory = get_subcategory(category, sub_name) if category else None
                subsubcategory = get_subsubcategory(subcategory, ssc_name) if subcategory else None
                return category, subcategory, subsubcategory
            if l1 in CATEGORY_ONLY_MAPPINGS:
                return get_category(CATEGORY_ONLY_MAPPINGS[l1]), None, None
            category = get_category(l1)
            subcategory = get_subcategory(category, l2) if category else None
            subsubcategory = get_subsubcategory(subcategory, l3) if subcategory else None
            return category, subcategory, subsubcategory

        stats = {
            "total": 0,
            "skipped_blank_model": 0,
            "created": 0,
            "updated": 0,
            "full_match": 0,
            "partial_match": 0,
            "no_match": 0,
            "flagged_for_review": 0,
        }
        unmatched_paths = set()

        parsed_rows = []
        with handle:
            reader = csv.DictReader(handle)
            for row in reader:
                stats["total"] += 1
                model_name = _clean(row.get("Model"), max_length=100)
                if not model_name:
                    stats["skipped_blank_model"] += 1
                    continue

                l1 = _clean(row.get("Menu Level 1"))
                l2 = _clean(row.get("Menu Level 2"))
                l3 = _clean(row.get("Menu Level 3"))
                category, subcategory, subsubcategory = resolve_leaf(l1, l2, l3)

                if subsubcategory:
                    stats["full_match"] += 1
                elif category:
                    stats["partial_match"] += 1
                    unmatched_paths.add(f"{l1} > {l2} > {l3}")
                else:
                    stats["no_match"] += 1
                    unmatched_paths.add(l1)

                parsed_rows.append(
                    {
                        "name": model_name,
                        "brand": _clean(row.get("Brand"), max_length=100),
                        "vendor_description": _clean(row.get("Description"), max_length=500),
                        "vendor_list_price": _parse_price(row.get("Your Final Price")),
                        "suggested_subsubcategory": subsubcategory,
                    }
                )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run -- nothing written to the database."))
        else:
            with transaction.atomic():
                for data in parsed_rows:
                    catalog_entry, created, matched_pattern = register_model_name(data["name"])
                    catalog_entry.brand = data["brand"]
                    catalog_entry.vendor_description = data["vendor_description"]
                    catalog_entry.vendor_list_price = data["vendor_list_price"]
                    catalog_entry.suggested_subsubcategory = data["suggested_subsubcategory"]
                    catalog_entry.from_vendor_catalog = True
                    catalog_entry.save(
                        update_fields=[
                            "brand",
                            "vendor_description",
                            "vendor_list_price",
                            "suggested_subsubcategory",
                            "from_vendor_catalog",
                        ]
                    )
                    stats["created" if created else "updated"] += 1
                    if matched_pattern is None and catalog_entry.needs_review:
                        stats["flagged_for_review"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Parsed {stats['total']} rows ({stats['skipped_blank_model']} skipped, no model name) -> "
                f"{stats['created']} created, {stats['updated']} updated. "
                f"Category mapping: {stats['full_match']} full match, {stats['partial_match']} "
                f"category-only, {stats['no_match']} no match. "
                f"{stats['flagged_for_review']} flagged needs_review (name didn't match any known pattern)."
            )
        )
        if unmatched_paths:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(unmatched_paths)} distinct category path(s) didn't fully resolve against "
                    "the existing Category/Subcategory/SubSubcategory tables (sample below). These "
                    "models still get brand/description/price, just no autofill category suggestion:"
                )
            )
            for path in sorted(unmatched_paths)[:50]:
                self.stdout.write(f"  - {path}")
