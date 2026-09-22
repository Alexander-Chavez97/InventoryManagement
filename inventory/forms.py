from django import forms

from .models import Item, ItemPhoto

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
        fields = ["serial_number", "item_type", "status", "location", "notes"]
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

    def clean_serial_number(self):
        return self.cleaned_data["serial_number"].strip()


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
    item_type = forms.ChoiceField(
        required=False,
        choices=[("", "All types")] + list(Item.ITEM_TYPES),
    )
