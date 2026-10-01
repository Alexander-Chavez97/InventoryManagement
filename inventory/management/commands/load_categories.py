from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from inventory.models import Category, Subcategory, SubSubcategory

# Exact filenames as they live in the project root (double spaces and all --
# must match on disk).
CATEGORY_FILES = [
    "Access Control.txt",
    "EnergyTools.txt",
    "Intrusion Systems.txt",
    "IoT  GPS  Telematics and Emergency.txt",
    "Networking.txt",
    "Radio Communication.txt",
    "Structured Cabling.txt",
    "Video Surveillance.txt",
]


def parse_category_file(path):
    """Parse one catalog .txt file into (category_name, {subcategory: [sub-subcategories]}).

    Format:
        Category Name
        <blank line>
         - Subcategory
          - Sub-subcategory
          - Sub-subcategory
        <blank line>
         - Subcategory
          - Sub-subcategory

    Indentation is tolerant (0-3 leading spaces observed in these files) --
    a blank line always ends the current subcategory's block, and within a
    block the FIRST bullet line is the subcategory, every bullet line after
    it is one of that subcategory's sub-subcategories.
    """
    with open(path, "r", encoding="utf-8-sig") as handle:
        lines = [line.rstrip("\n") for line in handle]

    category_name = None
    subcategories = {}
    current_sub = None

    for raw in lines:
        stripped = raw.strip()
        if category_name is None:
            if stripped:
                category_name = stripped
            continue
        if not stripped:
            current_sub = None  # blank line closes the current subcategory block
            continue
        text = stripped.lstrip("-*").strip()
        if not text:
            continue
        if current_sub is None:
            current_sub = text
            subcategories.setdefault(current_sub, [])
        else:
            subcategories[current_sub].append(text)

    if category_name is None:
        raise CommandError(f"{path}: no category name found (file looks empty)")
    return category_name, subcategories


class Command(BaseCommand):
    help = (
        "Load the Category / Subcategory / Sub-subcategory catalog from the "
        "*.txt files in the project root. Safe to re-run: existing rows are "
        "matched by name and left alone, only new ones are added."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=str(settings.BASE_DIR),
            help="Directory the catalog .txt files live in (default: project root).",
        )

    def handle(self, *args, **options):
        base = Path(options["dir"])
        new_categories = new_subcategories = new_subsubcategories = 0

        for filename in CATEGORY_FILES:
            path = base / filename
            if not path.exists():
                self.stderr.write(self.style.WARNING(f"Skipping missing file: {path}"))
                continue

            category_name, subcategories = parse_category_file(path)
            category, created = Category.objects.get_or_create(name=category_name)
            new_categories += int(created)

            for sub_name, subsub_names in subcategories.items():
                subcategory, created = Subcategory.objects.get_or_create(
                    category=category, name=sub_name
                )
                new_subcategories += int(created)

                for subsub_name in subsub_names:
                    _, created = SubSubcategory.objects.get_or_create(
                        subcategory=subcategory, name=subsub_name
                    )
                    new_subsubcategories += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                "Loaded catalog: "
                f"{new_categories} new categor{'y' if new_categories == 1 else 'ies'}, "
                f"{new_subcategories} new subcategor{'y' if new_subcategories == 1 else 'ies'}, "
                f"{new_subsubcategories} new sub-subcategor{'y' if new_subsubcategories == 1 else 'ies'}."
            )
        )
