from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.urls import reverse_lazy

from .models import Category, Item, ItemPhoto, Subcategory, SubSubcategory

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
        fields = ["serial_number", "category", "subcategory", "subsubcategory", "status", "location", "notes"]
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
        fields = ["status", "location", "notes"]
        widgets = {
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

class StaffUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm):
        model = User
        fields = ("username", "first_name", "last_name", "email")
