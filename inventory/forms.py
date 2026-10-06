from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.urls import reverse_lazy

from .models import Category, Item, ItemPhoto, Shipment, Subcategory, SubSubcategory

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(d, initial) for d in data]
        return single_file_clean(data, initial)


class ScannerIntakeForm(forms.ModelForm):
    category = forms.ModelChoiceField(
        queryset=Category.objects.order_by("name"),
        required=True,
        empty_label="Select a category…",
        label="Category",
    )
    subcategory = forms.ModelChoiceField(
        queryset=Subcategory.objects.none(),
        required=True,
        empty_label="Select a subcategory…",
        label="Subcategory",
    )
    photos = MultipleFileField(
        required=False,
        widget=MultipleFileInput(
            attrs={
                "accept": "image/*",
                "multiple": True,
                "id": "id_photos",
            }
        ),
        help_text="Take or attach barcode and item photos.",
    )

    class Meta:
        model = Item
        fields = ["serial_number", "quantity", "category", "subcategory", "subsubcategory", "status", "location", "notes"]
        labels = {
            "subsubcategory": "Sub-subcategory",
        }
        widgets = {
            "serial_number": forms.TextInput(
                attrs={
                    "autofocus": True,
                    "autocomplete": "off",
                    "inputmode": "text",
                    "placeholder": "Scan barcode or type serial",
                    "class": "scan-input",
                }
            ),
            "quantity": forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["subsubcategory"].queryset = SubSubcategory.objects.none()
        self.fields["subsubcategory"].required = True
        self.fields["subsubcategory"].empty_label = "Select a sub-subcategory…"

        self.fields["category"].widget.attrs.update(
            {
                "hx-get": reverse_lazy("inventory:subcategory_options"),
                "hx-target": "#subcategory-wrapper",
                "hx-swap": "innerHTML",
                "hx-trigger": "change",
            }
        )
        self.fields["subcategory"].widget.attrs.update(
            {
                "hx-get": reverse_lazy("inventory:subsubcategory_options"),
                "hx-target": "#subsubcategory-wrapper",
                "hx-swap": "innerHTML",
                "hx-trigger": "change",
            }
        )

        data = self.data if self.is_bound else None
        category_id = data.get("category") if data else None
        subcategory_id = data.get("subcategory") if data else None

        if not category_id and self.instance and self.instance.pk and self.instance.subsubcategory_id:
            leaf = self.instance.subsubcategory
            category_id = leaf.subcategory.category_id
            subcategory_id = leaf.subcategory_id
            self.fields["category"].initial = category_id
            self.fields["subcategory"].initial = subcategory_id

        if category_id:
            self.fields["subcategory"].queryset = Subcategory.objects.filter(
                category_id=category_id
            ).order_by("name")
        if subcategory_id:
            self.fields["subsubcategory"].queryset = SubSubcategory.objects.filter(
                subcategory_id=subcategory_id
            ).order_by("name")

    def clean_serial_number(self):
        return self.cleaned_data["serial_number"].strip()

    def clean(self):
        cleaned = super().clean()
        category = cleaned.get("category")
        subcategory = cleaned.get("subcategory")
        subsubcategory = cleaned.get("subsubcategory")
        if subcategory and category and subcategory.category_id != category.pk:
            self.add_error("subcategory", "Doesn't match the selected category.")
        if subsubcategory and subcategory and subsubcategory.subcategory_id != subcategory.pk:
            self.add_error("subsubcategory", "Doesn't match the selected subcategory.")
        return cleaned


class StatusChangeForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["quantity", "status", "location", "notes"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ItemPhotoForm(forms.Form):
    photos = MultipleFileField(
        required=True,
        widget=MultipleFileInput(
            attrs={
                "accept": "image/*",
                "multiple": True,
                "id": "id_more_photos",
            }
        ),
    )
    kind = forms.ChoiceField(choices=ItemPhoto.KIND_CHOICES, initial="DESCRIPTION")

class ItemFilterForm(forms.Form):
    q = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Search serial number",
                "autocomplete": "off",
                "class": "scan-input",
            }
        ),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[("", "All statuses")] + list(Item.STATUS_CHOICES),
    )
    category = forms.ModelChoiceField(
        required=False,
        queryset=Category.objects.order_by("name"),
        empty_label="All categories",
    )
    include_out_of_stock = forms.BooleanField(
        required=False,
        label="Show out-of-stock items",
    )

class StaffUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm):
        model = User
        fields = ("username", "first_name", "last_name", "email")


class ShipmentHeaderForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = ["client_name", "client_email", "job_reference", "notes"]
        labels = {
            "client_name": "Client name",
            "client_email": "Client email",
        }
        widgets = {
            "client_name": forms.TextInput(attrs={"placeholder": "Who this is going to"}),
            "client_email": forms.EmailInput(attrs={"placeholder": "Where to send the shipment notice"}),
            "job_reference": forms.TextInput(attrs={"placeholder": "PO number, job name, etc. (optional)"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ItemQuantitySelect(forms.Select):
    """A <select> of items that stamps each <option> with the item's current
    on-hand quantity as data-quantity, so the template's JS can show "0 of N
    total" next to the quantity field without a round trip to the server."""

    def __init__(self, *args, quantities=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.quantities = quantities or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        raw = value.value if hasattr(value, "value") else value
        if raw not in (None, ""):
            try:
                pk = int(raw)
            except (TypeError, ValueError):
                pk = None
            if pk is not None and pk in self.quantities:
                option["attrs"]["data-quantity"] = self.quantities[pk]
        return option


class ShipmentLineForm(forms.Form):
    """One row: an item and how much of it is going out. An entirely blank
    row (no item picked) is treated as unused, not an error -- this lets the
    formset always render a handful of spare rows."""

    item = forms.ModelChoiceField(
        queryset=Item.objects.filter(quantity__gt=0).order_by("serial_number"),
        required=False,
        empty_label="Select an item…",
        widget=ItemQuantitySelect,
    )
    quantity_shipped = forms.IntegerField(
        required=False,
        min_value=1,
        label="Qty",
        widget=forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item"].widget.quantities = {
            item.pk: item.quantity for item in self.fields["item"].queryset
        }

    def is_blank(self):
        data = getattr(self, "cleaned_data", None) or {}
        return not data.get("item") and not data.get("quantity_shipped")

    def clean(self):
        cleaned = super().clean()
        item = cleaned.get("item")
        quantity_shipped = cleaned.get("quantity_shipped")

        if item is None and quantity_shipped is None:
            return cleaned  # whole row left blank -- fine, just unused

        if item is None:
            self.add_error("item", "Choose an item, or leave this whole row blank.")
            return cleaned
        if quantity_shipped is None:
            self.add_error("quantity_shipped", "Enter a quantity, or leave this whole row blank.")
            return cleaned

        if quantity_shipped > item.quantity:
            self.add_error(
                "quantity_shipped",
                f"Only {item.quantity} on hand for {item.serial_number} -- can't ship {quantity_shipped}.",
            )
        return cleaned


class BaseShipmentLineFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        seen_item_ids = set()
        has_at_least_one_line = False
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            item = form.cleaned_data.get("item") if form.cleaned_data else None
            if item is None:
                continue
            has_at_least_one_line = True
            if item.pk in seen_item_ids:
                form.add_error(
                    "item",
                    "This item is already on another line in this shipment -- "
                    "combine them into a single line instead.",
                )
            seen_item_ids.add(item.pk)

        if not has_at_least_one_line:
            raise forms.ValidationError("Add at least one item to the shipment.")


ShipmentLineFormSet = forms.formset_factory(
    ShipmentLineForm,
    formset=BaseShipmentLineFormSet,
    extra=8,
    can_delete=True,
)
