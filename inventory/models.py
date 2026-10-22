import io
import logging
import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, models, transaction
from django.utils import timezone
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


class ModelNamePattern(models.Model):
    """A recognized model-name "shape" (e.g. letter prefix + digits + an
    optional -SUFFIX chain). This is what lets intake accept a brand-new
    model name that's never been seen before but is clearly formatted the
    way real products are -- add a row here when a new product line shows
    up with a different shape, no code change needed."""

    label = models.CharField(
        max_length=150,
        help_text='Short description, e.g. "Letter prefix + digits + optional -SUFFIX".',
    )
    regex = models.CharField(
        max_length=200,
        help_text="Must match the *entire* model name (re.fullmatch), case-insensitive.",
    )
    example = models.CharField(max_length=100, blank=True, help_text="One real example, for reference.")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["label"]

    def __str__(self):
        return self.label

    def matches(self, name):
        try:
            return re.fullmatch(self.regex, name, re.IGNORECASE) is not None
        except re.error:
            return False


class ModelCatalog(models.Model):
    """The running list of model names known to be real -- every name an
    item has ever been recorded under -- so the intake form can suggest
    from it, and a newly typed name can be sanity-checked against it.

    The fields below `added_at` are vendor reference data (currently
    populated from an EPCOM product export via the `import_epcom_catalog`
    management command, see inventory/management/commands/). They let the
    intake form auto-fill a category for a model that's brand new to this
    inventory but already known from the vendor catalog -- see
    `suggested_subsubcategory` and its use in views.model_lookup. None of
    this ever touches a real Item record directly."""

    name = models.CharField(max_length=100, unique=True, db_index=True)
    matched_pattern = models.ForeignKey(
        ModelNamePattern, on_delete=models.SET_NULL, null=True, blank=True, related_name="models"
    )
    needs_review = models.BooleanField(
        default=False,
        help_text="Didn't match any known naming pattern when it was first entered.",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    from_vendor_catalog = models.BooleanField(
        default=False,
        help_text="Imported from a vendor product export (e.g. EPCOM) rather than entered by hand through intake.",
    )
    brand = models.CharField(max_length=100, blank=True)
    vendor_description = models.CharField(max_length=500, blank=True)
    vendor_list_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    suggested_subsubcategory = models.ForeignKey(
        "SubSubcategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Advisory only -- a category leaf suggested by the vendor catalog import, used to pre-fill intake for a model never recorded here before. Never authoritative the way Item.subsubcategory is.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Model catalog"

    def __str__(self):
        return self.name


def register_model_name(name):
    """Call right after an item is saved with a new `name`. Looks up/creates
    the ModelCatalog entry for it and reports whether it was new, and if so
    whether it matched a known naming pattern. This never blocks -- the item
    is already saved by the time this runs -- it only decides what to tell
    the user and whether the new catalog entry gets flagged for a human to
    double check."""
    name = name.strip()
    existing = ModelCatalog.objects.filter(name__iexact=name).first()
    if existing:
        return existing, False, existing.matched_pattern

    matched_pattern = None
    for pattern in ModelNamePattern.objects.filter(is_active=True):
        if pattern.matches(name):
            matched_pattern = pattern
            break

    catalog_entry = ModelCatalog.objects.create(
        name=name,
        matched_pattern=matched_pattern,
        needs_review=matched_pattern is None,
    )
    return catalog_entry, True, matched_pattern


def find_or_create_lot(*, model_name, subsubcategory, status, location, quantity, changed_by=None, price=None):
    """Find an existing Item "lot" that's identical in every way except
    quantity -- same model name, same category leaf, same status, same
    location -- or create a new one. A "lot" groups identical units, so
    `quantity` is how many of that exact combination are on hand; changing
    only some of them (e.g. moving 1 of 5 to a different status) means
    splitting them into a second lot rather than editing the first one in
    place. Used both by intake (merge into an existing lot instead of
    always creating a new row) and by splitting quantity off to a new
    status."""
    existing = Item.objects.filter(
        model_name__iexact=model_name,
        subsubcategory=subsubcategory,
        status=status,
        location=location,
    ).first()
    if existing:
        return existing, False

    item = Item(
        model_name=model_name,
        subsubcategory=subsubcategory,
        status=status,
        location=location,
        quantity=quantity,
        price=price,
    )
    item._changed_by = changed_by
    item.save()
    return item, True


class Location(models.Model):
    building = models.CharField(max_length=100)
    aisle = models.CharField(max_length=50, blank=True)
    shelf = models.CharField(max_length=50, blank=True)
    bin = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["building", "aisle", "shelf", "bin"]

    def __str__(self):
        parts = [self.building, self.aisle, self.shelf, self.bin]
        return " - ".join([p for p in parts if p])


class Category(models.Model):
    """Top-level catalog category (Video Surveillance, Radio Communication, ...)."""

    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class Subcategory(models.Model):
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, related_name="subcategories"
    )
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Subcategories"
        unique_together = [("category", "name")]

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class SubSubcategory(models.Model):
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.CASCADE, related_name="subsubcategories"
    )
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Sub-subcategories"
        unique_together = [("subcategory", "name")]

    def __str__(self):
        return f"{self.subcategory.category.name} / {self.subcategory.name} / {self.name}"


class Item(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("PARTS", "For Parts"),
        ("REPAIR", "Pending Repair"),
        ("REVIEW", "Pending Review"),
    ]

    # Legacy classification, kept only for items recorded before the
    # category/subcategory/sub-subcategory system replaced it. No longer
    # shown on the intake form -- new items are classified via
    # `subsubcategory` instead. See Category/Subcategory/SubSubcategory.
    ITEM_TYPES = [
        ("CAMERA", "Camera"),
        ("RADIO", "Radio"),
        ("VEHICLE", "Vehicle"),
    ]

    # Not a unique serial per unit -- this is the product's model name/number
    # (e.g. "XE43-GEN3"), and many units can share one. `quantity` is what
    # tracks how many of that model are on hand.
    model_name = models.CharField(max_length=100, db_index=True)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Optional. Shown on the inventory list when set.",
    )
    item_type = models.CharField(max_length=50, choices=ITEM_TYPES, blank=True)
    subsubcategory = models.ForeignKey(
        SubSubcategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="items",
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="REVIEW", db_index=True
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            # find_or_create_lot()'s "does a matching lot already exist?"
            # lookup (model_name__iexact + subsubcategory + status +
            # location) runs on every intake and every status-split -- the
            # two hottest writes in the app. There's no index at all today
            # on the subsubcategory/status/location combination, so this is
            # a full table scan every time, and it gets slower as the
            # catalog grows. (model_name__iexact itself is fine on Postgres
            # without any special handling -- it compiles to
            # `model_name = UPPER(%s)`, a plain equality check against the
            # existing index on model_name, since values are already
            # normalized to uppercase on the way in.)
            models.Index(
                fields=["model_name", "subsubcategory", "status", "location"],
                name="item_lot_lookup_idx",
            ),
        ]

    @property
    def category_path(self):
        """Full breadcrumb through the new hierarchy, for display."""
        if self.subsubcategory_id:
            ssc = self.subsubcategory
            return f"{ssc.subcategory.category.name} → {ssc.subcategory.name} → {ssc.name}"
        if self.item_type:
            return self.get_item_type_display()
        return "Uncategorized"

    @property
    def display_name(self):
        if self.subsubcategory_id:
            type_str = (
                self.subsubcategory.subcategory.category.name.replace(" ", "").replace("/", "")
            )
        elif self.item_type:
            type_str = self.get_item_type_display().replace(" ", "")
        else:
            type_str = "Uncategorized"
        status_str = self.get_status_display().replace(" ", "")
        return f"{self.model_name}.{type_str}.{status_str}"

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        user = kwargs.pop("changed_by", getattr(self, "_changed_by", None))
        is_new = self.pk is None
        old_status = None
        if not is_new:
            old_status = (
                Item.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            )
        super().save(*args, **kwargs)
        if is_new:
            StatusHistory.objects.create(
                item=self,
                old_status="",
                new_status=self.status,
                changed_by=user,
            )
        elif old_status is not None and old_status != self.status:
            StatusHistory.objects.create(
                item=self,
                old_status=old_status,
                new_status=self.status,
                changed_by=user,
            )


class StatusHistory(models.Model):
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name="status_history"
    )
    old_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Status Histories"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.item.model_name}: {self.old_status or '—'} → {self.new_status}"


MAX_PHOTO_DIMENSION = 1600  # px, longest side
PHOTO_JPEG_QUALITY = 82


class ItemPhoto(models.Model):
    KIND_CHOICES = [
        ("BARCODE", "Barcode"),
        ("DESCRIPTION", "Description"),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="items/%Y/%m/")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="DESCRIPTION")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return f"{self.item.model_name} {self.get_kind_display()}"

    def save(self, *args, **kwargs):
        # Only on the way in, not on a later metadata-only save (e.g.
        # changing `kind`) -- this is a brand-new upload the first time it's
        # saved, and self.image is still the raw in-memory upload, so there's
        # nothing to re-process on subsequent saves anyway.
        if self.pk is None and self.image:
            self._resize_image()
        super().save(*args, **kwargs)

    def _resize_image(self):
        """Downscale/recompress on upload so full-resolution phone-camera
        photos (often several MB each) don't bloat the media folder and slow
        down every page that shows them. Never blocks the upload -- if
        Pillow can't process this file for any reason, the original is kept
        as-is and this just logs it."""
        try:
            img = Image.open(self.image)
            img.load()
            # Phone photos commonly store orientation as an EXIF tag rather
            # than physically rotating the pixels -- apply it before
            # resizing, or the re-saved copy loses that tag and comes out
            # sideways.
            img = ImageOps.exif_transpose(img)
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((MAX_PHOTO_DIMENSION, MAX_PHOTO_DIMENSION), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=PHOTO_JPEG_QUALITY, optimize=True)
            new_name = str(Path(self.image.name).with_suffix(".jpg"))
            self.image = ContentFile(buffer.getvalue(), name=new_name)
        except Exception:
            logger.exception(
                "Couldn't resize an uploaded photo (item=%s) -- keeping the original.",
                self.item_id,
            )


class OrderSequence(models.Model):
    """One row per calendar month ("YYMM"), tracking the last XXXX used in
    that month's Order.order_number values (ORD-YYMM-XXXX). Allocating a
    number locks this row (not the Order table) so concurrent order
    creation can never hand out the same number twice, and a new month
    just starts a fresh row back at 0001."""

    year_month = models.CharField(max_length=4, unique=True)
    last_seq = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.year_month} -> {self.last_seq:04d}"

    @classmethod
    def next_order_number(cls, *, when=None):
        """Allocate and return the next ORD-YYMM-XXXX string for `when`
        (defaults to now). Must be called from inside the same
        transaction.atomic() block that goes on to create the Order, same
        as the stock-locking already done in order_new."""
        when = when or timezone.localtime(timezone.now())
        year_month = when.strftime("%y%m")
        for _attempt in range(3):
            try:
                with transaction.atomic():
                    seq_row, _created = cls.objects.select_for_update().get_or_create(
                        year_month=year_month, defaults={"last_seq": 0}
                    )
                    seq_row.last_seq += 1
                    seq_row.save(update_fields=["last_seq"])
                    return f"ORD-{year_month}-{seq_row.last_seq:04d}"
            except IntegrityError:
                # Two requests raced to create the first row of a new
                # month -- one of the get_or_create()s lost the unique
                # race. Retry; the row now exists so the next pass locks
                # and increments it normally.
                continue
        raise RuntimeError("Could not allocate an order number -- too much contention.")


class Order(models.Model):
    """A single outbound order: a set of items sent to one client/job,
    with an emailed notice of what went out. The client is not a saved
    record -- just a name/email typed in on the order form each time."""

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    client_name = models.CharField(max_length=150)
    client_email = models.EmailField()
    job_reference = models.CharField(
        max_length=150, blank=True, help_text="PO number, job name/number, etc."
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="orders"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} -- {self.client_name}"

    @property
    def email_delivered(self):
        return self.email_sent_at is not None


class OrderLine(models.Model):
    """One item/quantity sent out as part of an Order."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="order_lines")
    quantity_shipped = models.PositiveIntegerField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity_shipped} x {self.item.model_name}"
