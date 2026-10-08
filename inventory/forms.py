from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.urls import reverse_lazy

from .models import Category, Item, ItemPhoto, Order, Subcategory, SubSubcategory

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
        fields = ["model_name", "quantity", "price", "category", "subcategory", "subsubcategory", "status", "location", "notes"]
        labels = {
            "model_name": "Model name",
            "subsubcategory": "Sub-subcategory",
        }
        widgets = {
            "model_name": forms.TextInput(
                attrs={
                    "autofocus": True,
                    "autocomplete": "off",
                    "inputmode": "text",
                    "placeholder": "Scan barcode or type model name",
                    "class": "scan-input",
                    "list": "model-name-suggestions",
                }
            ),
            "quantity": forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}),
            "price": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "Optional"}),
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

    def clean_model_name(self):
        # Normalized to uppercase so the catalog doesn't end up with
        # look-alike duplicates that only differ by case (e.g. "xe43-gen3"
        # vs "XE43-GEN3").
        return self.cleaned_data["model_name"].strip().upper()

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
        fields = ["quantity", "price", "status", "location", "notes"]
        widgets = {
            "quantity": forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}),
            "price": forms.NumberInput(attrs={"min": 0, "step": "0.01", "placeholder": "Optional"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class SplitStatusForm(forms.Form):
    """Move some (not necessarily all) of an item's quantity to a different
    status -- e.g. 1 of 5 goes to "For Parts" while the other 4 stay
    "Active". The moved quantity either joins an existing lot that already
    matches (same model/category/status/location) or starts a new one."""

    quantity = forms.IntegerField(min_value=1, label="How many")
    status = forms.ChoiceField(choices=Item.STATUS_CHOICES, label="New status")

    def __init__(self, *args, item=None, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        if item is not None:
            self.fields["status"].choices = [
                (code, label) for code, label in Item.STATUS_CHOICES if code != item.status
            ]

    def clean_status(self):
        status = self.cleaned_data["status"]
        if self.item is not None and status == self.item.status:
            raise forms.ValidationError(
                f"Already {self.item.get_status_display()} -- pick a different status to move some of it there."
            )
        return status

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if self.item is not None and quantity > self.item.quantity:
            raise forms.ValidationError(
                f"Only {self.item.quantity} on hand -- can't move {quantity}."
            )
        return quantity


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
                "placeholder": "Search model name",
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


class OrderHeaderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["client_name", "client_email", "job_reference", "notes"]
        labels = {
            "client_name": "Client name",
            "client_email": "Client email",
        }
        widgets = {
            "client_name": forms.TextInput(attrs={"placeholder": "Who this is going to"}),
            "client_email": forms.EmailInput(attrs={"placeholder": "Where to send the order notice"}),
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


class OrderLineForm(forms.Form):
    """One row: an item and how much of it is going out. An entirely blank
    row (no item picked) is treated as unused, not an error -- this lets the
    formset always render a handful of spare rows."""

    item = forms.ModelChoiceField(
        queryset=Item.objects.filter(quantity__gt=0).order_by("model_name"),
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
                f"Only {item.quantity} on hand for {item.model_name} -- can't ship {quantity_shipped}.",
            )
        return cleaned


class BaseOrderLineFormSet(forms.BaseFormSet):
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
                    "This item is already on another line in this order -- "
                    "combine them into a single line instead.",
                )
            seen_item_ids.add(item.pk)

        if not has_at_least_one_line:
            raise forms.ValidationError("Add at least one item to the order.")


OrderLineFormSet = forms.formset_factory(
    OrderLineForm,
    formset=BaseOrderLineFormSet,
    extra=8,
    can_delete=True,
)
