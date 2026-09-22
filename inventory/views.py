from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ItemFilterForm, ItemPhotoForm, ScannerIntakeForm, StatusChangeForm, StaffUserCreationForm
from .models import Item, ItemPhoto


def _save_photos(item, files, kind="DESCRIPTION"):
    for upload in files:
        if not upload:
            continue
        ItemPhoto.objects.create(item=item, image=upload, kind=kind)


def _filtered_items(request):
    form = ItemFilterForm(request.GET)
    items = Item.objects.select_related("location")
    if form.is_valid():
        query = form.cleaned_data.get("q")
        status = form.cleaned_data.get("status")
        item_type = form.cleaned_data.get("item_type")
        if query:
            items = items.filter(serial_number__icontains=query.strip())
        if status:
            items = items.filter(status=status)
        if item_type:
            items = items.filter(item_type=item_type)
    return form, items


def _item_list_context(request):
    form, items = _filtered_items(request)
    counts = Item.objects.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status="ACTIVE")),
        parts=Count("id", filter=Q(status="PARTS")),
        review=Count("id", filter=Q(status="REVIEW")),
    )
    return {
        "filter_form": form,
        "items": items,
        "counts": counts,
    }


@login_required
def item_list(request):
    context = _item_list_context(request)
    if request.htmx:
        return render(request, "inventory/partials/item_rows.html", context)
    return render(request, "inventory/item_list.html", context)


@login_required
def item_detail(request, pk):
    item = get_object_or_404(
        Item.objects.select_related("location").prefetch_related(
            "status_history__changed_by", "photos"
        ),
        pk=pk,
    )
    if request.method == "POST" and request.POST.get("intent") == "photos":
        photo_form = ItemPhotoForm(request.POST, request.FILES)
        print("DEBUG form.files:", photo_form.files, flush=True)
        print("DEBUG is_valid:", photo_form.is_valid(), flush=True)
        print("DEBUG errors:", photo_form.errors, flush=True)
        widget = photo_form.fields["photos"].widget
        print(
            "DEBUG widget value_from_datadict:",
            repr(widget.value_from_datadict(photo_form.data, photo_form.files, "photos")),
            flush=True,
        )
        if photo_form.is_valid():
            _save_photos(
                item,
                request.FILES.getlist("photos"),
                photo_form.cleaned_data["kind"],
            )
            messages.success(request, "Photos uploaded.")
            return redirect("inventory:item_detail", pk=item.pk)
        form = StatusChangeForm(instance=item)
    elif request.method == "POST":
        form = StatusChangeForm(request.POST, instance=item)
        photo_form = ItemPhotoForm()
        if form.is_valid():
            updated = form.save(commit=False)
            updated._changed_by = request.user
            updated.save()
            messages.success(request, f"Updated {updated.display_name}.")
            return redirect("inventory:item_detail", pk=item.pk)
    else:
        form = StatusChangeForm(instance=item)
        photo_form = ItemPhotoForm()
    return render(
        request,
        "inventory/item_detail.html",
        {"item": item, "form": form, "photo_form": photo_form},
    )


@login_required
def scanner_intake(request):
    if request.method == "POST":
        form = ScannerIntakeForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item._changed_by = request.user
            item.save()
            _save_photos(item, request.FILES.getlist("photos"))
            if request.htmx:
                response = render(
                    request,
                    "inventory/partials/intake_success.html",
                    {"item": item, "form": ScannerIntakeForm()},
                )
                response["HX-Trigger"] = "intake-success"
                return response
            messages.success(request, f"Intake recorded: {item.display_name}.")
            return redirect("inventory:intake")
        if request.htmx:
            return render(
                request,
                "inventory/partials/intake_form.html",
                {"form": form},
                status=422,
            )
    else:
        form = ScannerIntakeForm()
    return render(request, "inventory/intake.html", {"form": form})


@login_required
@require_POST
def quick_status(request, pk):
    item = get_object_or_404(Item, pk=pk)
    new_status = request.POST.get("status")
    valid = {code for code, _ in Item.STATUS_CHOICES}
    if new_status not in valid:
        return HttpResponse("Invalid status", status=400)
    item.status = new_status
    item._changed_by = request.user
    item.save()
    if request.htmx:
        _, items = _filtered_items(request)
        return render(
            request,
            "inventory/partials/item_rows.html",
            {"items": items},
        )
    messages.success(request, f"{item.serial_number} set to {item.get_status_display()}.")
    return redirect("inventory:item_list")

def _is_admin(user):
    return user.is_staff

@login_required
@user_passes_test(_is_admin)
def create_staff_user(request):
    if request.method == "POST":
        form = StaffUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_staff = False # belt-and-suspenders: this view can never create an admin
            user.is_superuser = False
            user.save()
            messages.success(request, f"Staff account created for {user.username}.")
            return redirect("inventory:create_staff")
    else:
        form = StaffUserCreationForm()
    return render(request, "inventory/create_staff.html", {"form": form})
