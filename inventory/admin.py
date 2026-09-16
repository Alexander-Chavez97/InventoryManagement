from django.contrib import admin

from .models import Item, ItemPhoto, Location, StatusHistory


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("building", "aisle", "shelf", "bin")
    search_fields = ("building", "aisle", "shelf", "bin")


class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ("old_status", "new_status", "changed_by", "changed_at")
    can_delete = False


class ItemPhotoInline(admin.TabularInline):
    model = ItemPhoto
    extra = 0


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = (
        "serial_number",
        "item_type",
        "status",
        "location",
        "updated_at",
        "display_name",
    )
    list_filter = ("status", "item_type")
    search_fields = ("serial_number", "notes")
    inlines = [ItemPhotoInline, StatusHistoryInline]
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("item", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("new_status",)
    search_fields = ("item__serial_number",)
    readonly_fields = ("item", "old_status", "new_status", "changed_by", "changed_at")
