import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ItemFilterForm,
    ItemPhotoForm,
    ScannerIntakeForm,
    ShipmentHeaderForm,
    ShipmentLineFormSet,
    StatusChangeForm,
    StaffUserCreationForm,
)
from .models import Item, ItemPhoto, Shipment, ShipmentLine, Subcategory, SubSubcategory

logger = logging.getLogger(__name__)


def _save_photos(item, files, kind="DESCRIPTION"):
    for upload in files:
        if not upload:
            continue
        ItemPhoto.objects.create(item=item, image=upload, kind=kind)


def _filtered_items(request):
    form = ItemFilterForm(request.GET)
    items = Item.objects.select_related(
        "location", "subsubcategory__subcategory__category"
    )
    include_out_of_stock = False
    if form.is_valid():
        query = form.cleaned_data.get("q")
        status = form.cleaned_data.get("status")
        category = form.cleaned_data.get("category")
        include_out_of_stock = form.cleaned_data.get("include_out_of_stock")
        if query:
            items = items.filter(serial_number__icontains=query.strip())
        if status:
            items = items.filter(status=status)
        if category:
            items = items.filter(subsubcategory__subcategory__category=category)
    if not include_out_of_stock:
        # Items shipped down to 0 drop out of the default view -- the record
        # itself (history, photos, serial number) is untouched, and checking
        # "Show out-of-stock items" above brings it right back.
        items = items.exclude(quantity=0)
    return form, items, include_out_of_stock


ITEMS_PER_PAGE = 25


def _item_list_context(request):
    form, items, include_out_of_stock = _filtered_items(request)
    # The stats bar's counts follow the same out-of-stock toggle as the list
    # below it, so "total" means "however many are currently in view."
    counts_source = Item.objects if include_out_of_stock else Item.objects.exclude(quantity=0)
    counts = counts_source.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status="ACTIVE")),
        parts=Count("id", filter=Q(status="PARTS")),
        review=Count("id", filter=Q(status="REVIEW")),
    )
    paginator = Paginator(items, ITEMS_PER_PAGE)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    return {
        "filter_form": form,
        "items": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "counts": counts,
    }


@login_required
def item_list(request):
    context = _item_list_context(request)
    if request.htmx:
        return render(request, "inventory/partials/item_results.html", context)
    return render(request, "inventory/item_list.html", context)


@login_required
def item_detail(request, pk):
    item = get_object_or_404(
        Item.objects.select_related(
            "location", "subsubcategory__subcategory__category"
        ).prefetch_related("status_history__changed_by", "photos", "shipment_lines__shipment"),
        pk=pk,
    )
    if request.method == "POST" and request.POST.get("intent") == "photos":
        photo_form = ItemPhotoForm(request.POST, request.FILES)
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
def subcategory_options(request):
    category_id = request.GET.get("category")
    subcategories = (
        Subcategory.objects.filter(category_id=category_id).order_by("name")
        if category_id
        else Subcategory.objects.none()
    )
    return render(
        request,
        "inventory/partials/subcategory_options.html",
        {"subcategories": subcategories},
    )


@login_required
def subsubcategory_options(request):
    subcategory_id = request.GET.get("subcategory")
    subsubcategories = (
        SubSubcategory.objects.filter(subcategory_id=subcategory_id).order_by("name")
        if subcategory_id
        else SubSubcategory.objects.none()
    )
    return render(
        request,
        "inventory/partials/subsubcategory_options.html",
        {"subsubcategories": subsubcategories},
    )


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
        context = _item_list_context(request)
        return render(request, "inventory/partials/item_results.html", context)
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


def _send_shipment_email(shipment):
    """Email the client what shipped. Failures are logged and left for the
    caller to notice via `shipment.email_delivered` -- a failed send should
    never roll back the inventory change that already happened."""
    if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
        logger.warning(
            "Shipment #%s: EMAIL_HOST is not set, so this email will only be "
            "printed to this log -- it is NOT actually being delivered to %s. "
            "Add EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD to .env to send real email.",
            shipment.pk,
            shipment.client_email,
        )
    logger.info(
        "Shipment #%s: sending shipment email to %s via backend=%s host=%s:%s",
        shipment.pk,
        shipment.client_email,
        settings.EMAIL_BACKEND,
        settings.EMAIL_HOST or "(none)",
        settings.EMAIL_PORT,
    )
    body = render_to_string(
        "inventory/email/shipment_notice.txt",
        {"shipment": shipment, "lines": shipment.lines.select_related("item")},
    )
    subject = "Shipment notice"
    if shipment.job_reference:
        subject = f"{subject} - {shipment.job_reference}"
    try:
        sent_count = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [shipment.client_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Shipment #%s: send_mail() raised -- email NOT sent", shipment.pk)
        return
    logger.info(
        "Shipment #%s: send_mail() returned %s (number of messages Django handed to the backend)",
        shipment.pk,
        sent_count,
    )
    shipment.email_sent_at = timezone.now()
    shipment.save(update_fields=["email_sent_at"])


@login_required
def shipment_new(request):
    """Build an outbound shipment, preview it, and only on an explicit
    second confirmation does anything get written: inventory is decremented,
    the Shipment/ShipmentLine rows are created, and the client is emailed.
    Nothing happens from the first (preview) submission alone."""
    if request.method == "POST":
        header_form = ShipmentHeaderForm(request.POST)
        formset = ShipmentLineFormSet(request.POST, prefix="line")
        is_confirm = request.POST.get("confirm") == "1"

        if header_form.is_valid() and formset.is_valid():
            lines = [
                (form.cleaned_data["item"], form.cleaned_data["quantity_shipped"])
                for form in formset.forms
                if not formset._should_delete_form(form) and form.cleaned_data.get("item")
            ]

            if not is_confirm:
                # Preview only -- replay the exact submitted data as hidden
                # fields so the confirm step re-validates it fresh rather
                # than trusting anything from this request.
                hidden_fields = [
                    (key, value)
                    for key, values in request.POST.lists()
                    for value in values
                    if key != "csrfmiddlewaretoken"
                ]
                lines_with_remaining = [
                    (item, qty, item.quantity - qty) for item, qty in lines
                ]
                return render(
                    request,
                    "inventory/shipment_review.html",
                    {
                        "client_name": header_form.cleaned_data["client_name"],
                        "client_email": header_form.cleaned_data["client_email"],
                        "job_reference": header_form.cleaned_data["job_reference"],
                        "notes": header_form.cleaned_data["notes"],
                        "lines": lines_with_remaining,
                        "hidden_fields": hidden_fields,
                    },
                )

            # Confirm step: re-check stock under a lock in case it changed
            # since the preview was rendered (another shipment, an edit,
            # someone with two tabs open), then commit everything together.
            with transaction.atomic():
                locked_lines = []
                for item, qty in lines:
                    fresh_item = Item.objects.select_for_update().get(pk=item.pk)
                    if qty > fresh_item.quantity:
                        messages.error(
                            request,
                            f"Only {fresh_item.quantity} of {fresh_item.serial_number} left now "
                            "-- someone else may have changed it since you reviewed this "
                            "shipment. Please build it again.",
                        )
                        return redirect("inventory:shipment_new")
                    locked_lines.append((fresh_item, qty))

                shipment = Shipment.objects.create(
                    client_name=header_form.cleaned_data["client_name"],
                    client_email=header_form.cleaned_data["client_email"],
                    job_reference=header_form.cleaned_data["job_reference"],
                    notes=header_form.cleaned_data["notes"],
                    created_by=request.user,
                )
                for fresh_item, qty in locked_lines:
                    ShipmentLine.objects.create(
                        shipment=shipment, item=fresh_item, quantity_shipped=qty
                    )
                    fresh_item.quantity -= qty
                    fresh_item.save(update_fields=["quantity"])

            _send_shipment_email(shipment)
            if shipment.email_delivered:
                messages.success(
                    request, f"Shipment recorded and emailed to {shipment.client_email}."
                )
            else:
                messages.warning(
                    request,
                    f"Shipment recorded and inventory updated, but the email to "
                    f"{shipment.client_email} failed to send. You can resend it from "
                    "the shipment page.",
                )
            return redirect("inventory:shipment_detail", pk=shipment.pk)
    else:
        header_form = ShipmentHeaderForm()
        formset = ShipmentLineFormSet(prefix="line")

    return render(
        request,
        "inventory/shipment_form.html",
        {"header_form": header_form, "formset": formset},
    )


@login_required
def shipment_list(request):
    shipments = Shipment.objects.select_related("created_by").prefetch_related("lines__item")
    return render(request, "inventory/shipment_list.html", {"shipments": shipments})


@login_required
def shipment_detail(request, pk):
    shipment = get_object_or_404(
        Shipment.objects.select_related("created_by").prefetch_related("lines__item"),
        pk=pk,
    )
    return render(request, "inventory/shipment_detail.html", {"shipment": shipment})


@login_required
@require_POST
def shipment_resend_email(request, pk):
    shipment = get_object_or_404(Shipment, pk=pk)
    _send_shipment_email(shipment)
    if shipment.email_delivered:
        messages.success(request, f"Resent to {shipment.client_email}.")
    else:
        messages.error(request, "Still failed to send -- check the email settings.")
    return redirect("inventory:shipment_detail", pk=shipment.pk)
