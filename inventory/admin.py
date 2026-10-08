from django.contrib import admin

from .models import (
    Category,
    Item,
    ItemPhoto,
    Location,
    ModelCatalog,
    ModelNamePattern,
    Order,
    OrderLine,
    StatusHistory,
    Subcategory,
    SubSubcategory,
)


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("building", "aisle", "shelf", "bin")
    search_fields = ("building", "aisle", "shelf", "bin")


class SubcategoryInline(admin.TabularInline):
    model = Subcategory
    extra = 0
    show_change_link = True


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    inlines = [SubcategoryInline]


class SubSubcategoryInline(admin.TabularInline):
    model = SubSubcategory
    extra = 0


@admin.register(Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category")
    list_filter = ("category",)
    search_fields = ("name",)
    inlines = [SubSubcategoryInline]


@admin.register(SubSubcategory)
class SubSubcategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "subcategory", "category_name")
    list_filter = ("subcategory__category",)
    search_fields = ("name",)

    @admin.display(description="Category", ordering="subcategory__category__name")
    def category_name(self, obj):
        return obj.subcategory.category.name


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
        "model_name",
        "quantity",
        "price",
        "category_path",
        "status",
        "location",
        "updated_at",
        "display_name",
    )
    list_editable = ("quantity", "price")
    list_filter = ("status", "subsubcategory__subcategory__category", "item_type")
    search_fields = ("model_name", "notes")
    autocomplete_fields = ["subsubcategory"]
    inlines = [ItemPhotoInline, StatusHistoryInline]
    readonly_fields = ("created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ModelNamePattern)
class ModelNamePatternAdmin(admin.ModelAdmin):
    list_display = ("label", "regex", "example", "is_active")
    list_editable = ("is_active",)
    search_fields = ("label", "regex", "example")


@admin.register(ModelCatalog)
class ModelCatalogAdmin(admin.ModelAdmin):
    list_display = ("name", "matched_pattern", "needs_review", "added_at")
    list_filter = ("needs_review", "matched_pattern")
    search_fields = ("name",)
    actions = ["mark_reviewed"]

    @admin.action(description="Mark selected as reviewed")
    def mark_reviewed(self, request, queryset):
        queryset.update(needs_review=False)


@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("item", "old_status", "new_status", "changed_by", "changed_at")
    list_filter = ("new_status",)
    search_fields = ("item__model_name",)
    readonly_fields = ("item", "old_status", "new_status", "changed_by", "changed_at")


class OrderLineInline(admin.TabularInline):
    model = OrderLine
    extra = 0
    autocomplete_fields = ["item"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "client_name", "client_email", "job_reference", "created_by", "created_at", "email_delivered")
    search_fields = ("client_name", "client_email", "job_reference")
    inlines = [OrderLineInline]
    readonly_fields = ("created_by", "created_at", "email_sent_at")

    @admin.display(boolean=True, description="Emailed")
    def email_delivered(self, obj):
        return obj.email_delivered
