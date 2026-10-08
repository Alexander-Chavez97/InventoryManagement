"""Seed the ModelCatalog with real model names pulled from Epcom's live
product catalog (epcom.net), plus the naming-pattern "shapes" those models
follow, so intake recognizes them as known instead of flagging every one
for review.

This is a representative sample, not Epcom's full SKU list: Epcom isn't a
single manufacturer, it's a distributor carrying dozens of third-party
brands across many paginated departments (some 15+ pages deep), and
there's no bulk export of the whole thing. What's below was gathered by
browsing the first page of each of Epcom's 8 top-level departments --
chosen to line up 1:1 with CATEGORY_FILES in load_categories.py -- and
noting the brands and model shapes that showed up most. Re-run this
command later with more samples (or narrower, single-department passes)
to extend it; it's safe to re-run as-is since register_model_name()
no-ops on a name that's already in the catalog.

A few radio listings on Epcom's site show a model plus a trailing
region/variant suffix separated by a space (e.g. "F2100D 63 USA" for a
single-band vs. multi-band variant of the same radio). Only the leading
token -- the actual model -- is kept below; the suffix is a sales variant,
not part of the model name.
"""

from django.core.management.base import BaseCommand

from inventory.models import ModelNamePattern, register_model_name

# New naming "shape" this sample surfaced that the original seed pattern
# (migration 0009, "Letter prefix + digits + optional -SUFFIX chain")
# doesn't cover: dash- or slash-delimited alphanumeric codes that don't
# start with a clean letter-then-digit run (e.g. LinkedPro's "PL12DC3ABK",
# AccessPro's "SYSCA-4R-4D", numeric catalog numbers like "906-103-63"),
# optionally ending in a parenthetical suffix like "(P)" or "(R)".
NEW_PATTERNS = [
    {
        "label": "Dash/slash-delimited alphanumeric code, optional (suffix)",
        "regex": r"[A-Z0-9./]{1,20}(-[A-Z0-9./]+){0,6}(\([A-Z0-9]+\))?",
        "example": "PL12DC3ABK",
    },
]

# Source: epcom.net, first page of each department, 2026-10-08.
# Keyed by the same 8 departments as CATEGORY_FILES in load_categories.py.
EPCOM_MODELS = {
    "Access Control": [
        "ACVP2W-GEN3", "SYSCA-4R-4D", "F12", "K-30", "PLK12DC20ABK",
        "PL12DC5ABK", "MAG600NLED", "PL12DC3ABK", "APX-4000", "SF-22-04-LE",
        "SF-300", "PL-12-12", "TF-1700", "GDS-3710", "PROR400",
        "PLK12DC8ABK", "R20KSIP", "E16C", "MC-1000", "R20A",
    ],
    "EnergyTools": [
        "PS-12-DC-4C", "LK712", "WI-PS306GF-UPS", "PL-110-D12", "NID",
        "PS12DC4P", "RT1640L", "AC-CORD-1.8M", "SS-18", "XP-16DC-20-4KV",
        "XP8-DC-16-4KV", "EPU600L", "PS-12DC4KV", "XP18DC30HD",
        "PL100D12V2", "LK9.512",
    ],
    "Intrusion Systems": [
        "AXMC", "AXKF", "AXDPI15(P)", "SF2033", "6164-SP", "GMR101",
        "MN01-LTE-M", "AXSE(R)", "SHELLYPLUS1UL", "SFPASO", "SF-22-02-LE",
        "SF-22-04", "SF-22-06-LE", "SF-206P", "PH81-ST", "AX-KH", "AXOL",
        "DS-PDP15P-EG2-WB", "AXOH",
    ],
    "IoT  GPS  Telematics and Emergency": [
        "E92004", "5550CA-VM", "ED3801A", "ED3777-A", "7965A", "5580CA",
        "ED3802AB", "T366G", "A57MVT600", "ED3704-A", "ED3802AC",
        "RUPTELATEMP", "ED3704-B", "CL199XH", "LPROLIC", "BAT002",
        "ACGPS08", "A58MVT380", "A57MVT380", "GT06EUSB",
    ],
    "Networking": [
        "WI-CPE513P-KIT-V3", "WI-PS526G", "A5X", "LPCM-042U", "POE-E304",
        "FDP-420F", "WI-PS205HV2", "N5-X16", "AP6-PRO", "B24",
        "AP6-AL", "GXP-1625", "GXP-2170", "GXP-1628", "ROUTE10",
        "SR-1924-GFP",
    ],
    "Radio Communication": [
        # Leading token only -- see module docstring re: trailing region/
        # variant suffixes dropped from the distributor's display names.
        "F200", "HM-152", "F2100D", "F3001", "A25C86USA", "A12024USA",
        "F5021", "F1100D", "F2000S", "IC-M73PLUS", "F6021", "BP-232H",
        "F1000", "F2100D66USA", "F1000S", "F2100DS", "M25BLUE51USA",
        "906-103-63", "156-000-1241", "M3731USA",
    ],
    "Structured Cabling": [
        "LP-6060-24U-R2", "LP-FOM3-LCLC-R02", "NC-100", "LP-80100-42UR2",
        "LP-UT6-150-BK-28", "LP-PG8-025-WH", "GW-44-024", "TP-0-KIT",
        "LPPPCS6A48P", "LPCV-45D", "TG-BUS-G10", "OFL100", "TEK-100",
        "AGRUPATHOR-19-N", "EP25P", "TC5S/100", "488-SO-162", "ZM6A-S10-06",
        "GW-44-006", "LPKJUC6A",
    ],
    "Video Surveillance": [
        "E8-TURBO-GEN3", "XE43D-GEN3", "EP-CAT-5E-V2", "B8-TURBO-C",
        "XB54CA-GEN3", "PRO-CAT-6-EXT-LITE", "XPTIE254-GEN3",
        "EP-CAT-5E-V2P", "PRO-CAT-5E-GELX", "XMRX5", "TT-101-PV-TURBO",
        "PL-DC-1000", "XR432/16-GEN3", "PRO-CAT-6-PLUS", "TT101FTURBOZ",
        "E4K-TURBO-C", "PRO-CAT-6", "PRO-RG-59V",
    ],
}


class Command(BaseCommand):
    help = (
        "Seed ModelCatalog with a representative sample of real Epcom model "
        "names (and add the naming patterns those models follow), so intake "
        "recognizes them instead of flagging every one for review. Safe to "
        "re-run: existing catalog entries and patterns are left alone."
    )

    def handle(self, *args, **options):
        new_patterns = 0
        for pattern_data in NEW_PATTERNS:
            _, created = ModelNamePattern.objects.get_or_create(
                regex=pattern_data["regex"],
                defaults={
                    "label": pattern_data["label"],
                    "example": pattern_data["example"],
                    "is_active": True,
                },
            )
            new_patterns += int(created)

        new_models = 0
        already_known = 0
        flagged_for_review = []
        for category, names in EPCOM_MODELS.items():
            for name in names:
                catalog_entry, created, matched_pattern = register_model_name(name)
                if not created:
                    already_known += 1
                    continue
                new_models += 1
                if matched_pattern is None:
                    flagged_for_review.append((category, name))

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded Epcom models: {new_patterns} new naming pattern(s), "
                f"{new_models} new catalog entries, {already_known} already known."
            )
        )
        if flagged_for_review:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(flagged_for_review)} new entries didn't match any "
                    "known pattern and are flagged needs_review:"
                )
            )
            for category, name in flagged_for_review:
                self.stdout.write(f"  - {name} ({category})")
