from django.conf import settings
from django.db import models


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

    serial_number = models.CharField(max_length=100, unique=True, db_index=True)
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
        return f"{self.serial_number}.{type_str}.{status_str}"

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
        return f"{self.item.serial_number}: {self.old_status or '—'} → {self.new_status}"


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
        return f"{self.item.serial_number} {self.get_kind_display()}"
