import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from openpyxl import Workbook
from openpyxl.styles import Font
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ItemFilterForm,
    ItemPhotoForm,
    OrderFilterForm,
    OrderHeaderForm,
    OrderLineFormSet,
    ScannerIntakeForm,
    SplitStatusForm,
    StatusChangeForm,
    SupervisorUserCreationForm,
)
from .models import (
    Category,
    Item,
    ItemPhoto,
    ModelCatalog,
    OrderSequence,
    Order,
    OrderLine,
    Subcategory,
    SubSubcategory,
    find_or_create_lot,
    register_model_name,
)

logger = logging.getLogger(__name__)


def _save_photos(item, files, kind="DESCRIPTION"):
    for upload in files:
        if not upload:
            continue
        ItemPhoto.objects.create(item=item, image=upload, kind=kind)


def _catalog_names():
    return list(ModelCatalog.objects.order_by("name").values_list("name", flat=True))


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
            items = items.filter(model_name__icontains=query.strip())
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
        "category_count": Category.objects.count(),
    }


@login_required
def item_list(request):
    context = _item_list_context(request)
    if request.htmx:
        return render(request, "inventory/partials/item_results.html", context)
    return render(request, "inventory/item_list.html", context)


def _xlsx_response(filename, headers, rows):
    """Build a one-sheet .xlsx download from already-fetched header/row
    data. Shared by item_export/order_export -- bold header row, columns
    sized to fit their content, and a Content-Disposition that makes the
    browser download it instead of trying to render it in place."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)

    for column_cells in sheet.columns:
        longest = max((len(str(cell.value)) if cell.value is not None else 0) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(longest + 2, 10), 40)

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@login_required
def item_export(request):
    """Same filters as the inventory search page (see _filtered_items) --
    exports exactly what's currently in view there, not the whole table."""
    _, items, _ = _filtered_items(request)
    headers = ["Model Name", "Category", "Status", "Quantity", "Location", "Price", "Updated"]
    rows = [
        [
            item.model_name,
            item.category_path,
            item.get_status_display(),
            item.quantity,
            str(item.location) if item.location_id else "",
            float(item.price) if item.price is not None else None,
            timezone.localtime(item.updated_at).strftime("%Y-%m-%d %H:%M"),
        ]
        for item in items
    ]
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M")
    return _xlsx_response(f"inventory-search-{timestamp}.xlsx", headers, rows)


@login_required
def order_export(request):
    """Same filters as the order list page (see _filtered_orders) --
    exports exactly what's currently in view there, not every order."""
    _, orders = _filtered_orders(request)
    headers = ["Order #", "Date", "Client Name", "Client Email", "Job Reference", "Items", "Email Sent", "Created By"]
    rows = [
        [
            order.order_number,
            timezone.localtime(order.created_at).strftime("%Y-%m-%d"),
            order.client_name,
            order.client_email,
            order.job_reference,
            order.item_count,
            "Yes" if order.email_delivered else "No",
            str(order.created_by) if order.created_by_id else "",
        ]
        for order in orders
    ]
    timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d-%H%M")
    return _xlsx_response(f"orders-{timestamp}.xlsx", headers, rows)


@login_required
def item_detail(request, pk):
    item = get_object_or_404(
        Item.objects.select_related(
            "location", "subsubcategory__subcategory__category"
        ).prefetch_related("status_history__changed_by", "photos", "order_lines__order"),
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
        {
            "item": item,
            "form": form,
            "photo_form": photo_form,
            "split_form": SplitStatusForm(item=item),
        },
    )


@login_required
def item_detail_panel(request, pk):
    """Rendered into the item list's right-hand rail when a row is clicked --
    a trimmed-down view of item_detail for quick glancing without leaving
    the search results."""
    item = get_object_or_404(
        Item.objects.select_related(
            "location", "subsubcategory__subcategory__category"
        ).prefetch_related("status_history__changed_by", "photos"),
        pk=pk,
    )
    status_labels = dict(Item.STATUS_CHOICES)
    history = [
        {
            "old_label": status_labels.get(event.old_status, event.old_status)
            if event.old_status
            else "Created",
            "new_label": status_labels.get(event.new_status, event.new_status),
            "changed_at": event.changed_at,
            "changed_by": event.changed_by,
        }
        for event in item.status_history.all()
    ]
    return render(
        request,
        "inventory/partials/item_detail_panel.html",
        {"item": item, "history": history},
    )


@login_required
def item_detail_panel_clear(request):
    return render(request, "inventory/partials/item_detail_empty.html", {})


@login_required
@require_POST
def item_split_status(request, pk):
    """Move some (not necessarily all) of an item's on-hand quantity to a
    different status, without touching the rest. The moved quantity joins
    whatever lot already matches the new status/location, or starts a new
    one -- same matching rule as intake merging."""
    item = get_object_or_404(Item, pk=pk)
    form = SplitStatusForm(request.POST, item=item)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("inventory:item_detail", pk=item.pk)

    quantity = form.cleaned_data["quantity"]
    new_status = form.cleaned_data["status"]

    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item.pk)
        if new_status == item.status:
            messages.error(
                request,
                f"Already {item.get_status_display()} now -- someone else may have changed "
                "it since this page loaded. Please try again.",
            )
            return redirect("inventory:item_detail", pk=item.pk)
        if quantity > item.quantity:
            messages.error(
                request,
                f"Only {item.quantity} on hand now -- someone else may have changed it "
                "since this page loaded. Please try again.",
            )
            return redirect("inventory:item_detail", pk=item.pk)

        item.quantity -= quantity
        item._changed_by = request.user
        item.save(update_fields=["quantity"])

        destination, dest_created = find_or_create_lot(
            model_name=item.model_name,
            subsubcategory=item.subsubcategory,
            status=new_status,
            location=item.location,
            quantity=quantity,
            changed_by=request.user,
            price=item.price,
        )
        if not dest_created:
            destination.quantity += quantity
            destination._changed_by = request.user
            destination.save(update_fields=["quantity"])

    messages.success(
        request,
        f"Moved {quantity} of {item.model_name} to {destination.get_status_display()}.",
    )
    return redirect("inventory:item_detail", pk=item.pk)


@login_required
def scanner_intake(request):
    if request.method == "POST":
        form = ScannerIntakeForm(request.POST, request.FILES)
        if form.is_valid():
            cleaned = form.cleaned_data
            item, created = find_or_create_lot(
                model_name=cleaned["model_name"],
                subsubcategory=cleaned["subsubcategory"],
                status=cleaned["status"],
                location=cleaned.get("location"),
                quantity=cleaned["quantity"],
                changed_by=request.user,
                price=cleaned.get("price"),
            )
            merged = not created
            if merged:
                # Same model, same category, same status, same location as
                # an existing lot -- add to it instead of creating a
                # duplicate row.
                update_fields = ["quantity"]
                item.quantity += cleaned["quantity"]
                if item.price is None and cleaned.get("price") is not None:
                    item.price = cleaned["price"]
                    update_fields.append("price")
                item._changed_by = request.user
                item.save(update_fields=update_fields)
            _save_photos(item, request.FILES.getlist("photos"))
            _catalog_entry, is_new_model, matched_pattern = register_model_name(item.model_name)

            if merged:
                base_message = f"Added {cleaned['quantity']} more {item.model_name} -- now {item.quantity} total."
                if cleaned.get("notes"):
                    base_message += (
                        " (The notes you typed weren't saved -- this merged into an "
                        "existing item, which keeps its own notes.)"
                    )
            else:
                base_message = f"Intake recorded: {item.display_name}."

            if request.htmx:
                response = render(
                    request,
                    "inventory/partials/intake_success.html",
                    {
                        "item": item,
                        "form": ScannerIntakeForm(),
                        "catalog_names": _catalog_names(),
                        "is_new_model": is_new_model,
                        "matched_pattern": matched_pattern,
                        "merged": merged,
                        "merged_quantity": cleaned["quantity"],
                    },
                )
                response["HX-Trigger"] = "intake-success"
                return response
            if is_new_model and matched_pattern is None:
                messages.warning(
                    request,
                    f"{base_message} Heads up -- "
                    f'"{item.model_name}" doesn\'t match any known model format, so '
                    "it's been flagged for review in the model catalog.",
                )
            elif is_new_model:
                messages.success(
                    request,
                    f'{base_message} New model "{item.model_name}" added to the catalog.',
                )
            else:
                messages.success(request, base_message)
            return redirect("inventory:intake")
        if request.htmx:
            return render(
                request,
                "inventory/partials/intake_form.html",
                {"form": form, "catalog_names": _catalog_names()},
                status=422,
            )
    else:
        form = ScannerIntakeForm()
    return render(request, "inventory/intake.html", {"form": form, "catalog_names": _catalog_names()})


@login_required
def model_lookup(request):
    """Looked up live as the intake form's model name changes (see
    static/js/intake.js + the model_name field's hx- attrs in forms.py).
    Shows whatever's already on record under that name so a repeat intake
    doesn't silently create a parallel lot next to an existing one, and --
    when every existing lot for this model agrees on one category -- auto-
    fills category/subcategory/sub-subcategory instead of making the user
    re-pick them. Location is deliberately left alone: the same model can
    legitimately live in more than one bin."""
    model_name = request.GET.get("model_name", "").strip()
    matches = []
    category = subcategory = subsubcategory = None
    category_source = None
    catalog_entry = None

    if model_name:
        matches = list(
            Item.objects.filter(model_name__iexact=model_name)
            .select_related("subsubcategory__subcategory__category", "location")
            .order_by("-updated_at")
        )
        leaf_ids = {item.subsubcategory_id for item in matches if item.subsubcategory_id}
        if len(leaf_ids) == 1:
            subsubcategory = matches[0].subsubcategory
            subcategory = subsubcategory.subcategory
            category = subcategory.category
            category_source = "inventory"

        # No single authoritative category from our own records (either this
        # model has never been seen, or existing lots disagree) -- fall back
        # to the EPCOM vendor catalog. Advisory only: ModelCatalog.brand /
        # vendor_description / vendor_list_price / suggested_subsubcategory
        # are never treated as confirmed inventory, so the template always
        # shows this as a suggestion to double-check rather than a fact.
        catalog_entry = ModelCatalog.objects.filter(name__iexact=model_name).select_related(
            "suggested_subsubcategory__subcategory__category"
        ).first()
        if category is None and catalog_entry and catalog_entry.suggested_subsubcategory:
            subsubcategory = catalog_entry.suggested_subsubcategory
            subcategory = subsubcategory.subcategory
            category = subcategory.category
            category_source = "vendor"

    return render(
        request,
        "inventory/partials/model_lookup.html",
        {
            "model_name": model_name,
            "matches": matches,
            "category": category,
            "subcategory": subcategory,
            "subsubcategory": subsubcategory,
            "category_source": category_source,
            "catalog_entry": catalog_entry,
            "categories": Category.objects.order_by("name"),
            "subcategories": (
                Subcategory.objects.filter(category=category).order_by("name")
                if category
                else Subcategory.objects.none()
            ),
            "subsubcategories": (
                SubSubcategory.objects.filter(subcategory=subcategory).order_by("name")
                if subcategory
                else SubSubcategory.objects.none()
            ),
        },
    )


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
    messages.success(request, f"{item.model_name} set to {item.get_status_display()}.")
    return redirect("inventory:item_list")

def _is_admin(user):
    """Owner-only gate. Owner == is_staff (so Django admin login works) and
    is_superuser (so admin permissions aren't a separate thing to manage)
    together -- see the UserAdmin override in admin.py, which keeps those
    two flags in lockstep going forward so there's never a third, hybrid
    tier. Checking is_staff alone is enough here since nothing in this app
    can produce is_staff=True without is_superuser=True also being true."""
    return user.is_staff

@login_required
@user_passes_test(_is_admin)
def create_staff_user(request):
    """Owner-only: create a new Supervisor account. Supervisor is the only
    tier this view (or any in-app UI) can create -- full day-to-day access
    to intake/orders/search, never Django admin or the ability to create
    more accounts. Owner accounts are provisioned out of band."""
    if request.method == "POST":
        form = SupervisorUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_staff = False # belt-and-suspenders: this view can never create an Owner
            user.is_superuser = False
            user.save()
            messages.success(request, f"Supervisor account created for {user.username}.")
            return redirect("inventory:create_staff")
    else:
        form = SupervisorUserCreationForm()
    return render(request, "inventory/create_staff.html", {"form": form})


def _send_order_email(order):
    """Email the client what shipped. Failures are logged and left for the
    caller to notice via `order.email_delivered` -- a failed send should
    never roll back the inventory change that already happened."""
    if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
        logger.warning(
            "Order #%s: EMAIL_HOST is not set, so this email will only be "
            "printed to this log -- it is NOT actually being delivered to %s. "
            "Add EMAIL_HOST/EMAIL_HOST_USER/EMAIL_HOST_PASSWORD to .env to send real email.",
            order.pk,
            order.client_email,
        )
    logger.info(
        "Order #%s: sending order email to %s via backend=%s host=%s:%s",
        order.pk,
        order.client_email,
        settings.EMAIL_BACKEND,
        settings.EMAIL_HOST or "(none)",
        settings.EMAIL_PORT,
    )
    body = render_to_string(
        "inventory/email/order_notice.txt",
        {"order": order, "lines": order.lines.select_related("item")},
    )
    subject = f"Order {order.order_number}"
    if order.job_reference:
        subject = f"{subject} - {order.job_reference}"
    try:
        sent_count = send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [order.client_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Order #%s: send_mail() raised -- email NOT sent", order.pk)
        return
    logger.info(
        "Order #%s: send_mail() returned %s (number of messages Django handed to the backend)",
        order.pk,
        sent_count,
    )
    order.email_sent_at = timezone.now()
    order.save(update_fields=["email_sent_at"])


@login_required
def order_new(request):
    """Build an outbound order, preview it, and only on an explicit
    second confirmation does anything get written: inventory is decremented,
    the Order/OrderLine rows are created, and the client is emailed.
    Nothing happens from the first (preview) submission alone."""
    if request.method == "POST":
        header_form = OrderHeaderForm(request.POST)
        formset = OrderLineFormSet(request.POST, prefix="line")
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
                    "inventory/order_review.html",
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
            # since the preview was rendered (another order, an edit,
            # someone with two tabs open), then commit everything together.
            with transaction.atomic():
                locked_lines = []
                for item, qty in lines:
                    fresh_item = Item.objects.select_for_update().get(pk=item.pk)
                    if qty > fresh_item.quantity:
                        messages.error(
                            request,
                            f"Only {fresh_item.quantity} of {fresh_item.model_name} left now "
                            "-- someone else may have changed it since you reviewed this "
                            "order. Please build it again.",
                        )
                        return redirect("inventory:order_new")
                    locked_lines.append((fresh_item, qty))

                order = Order.objects.create(
                    order_number=OrderSequence.next_order_number(),
                    client_name=header_form.cleaned_data["client_name"],
                    client_email=header_form.cleaned_data["client_email"],
                    job_reference=header_form.cleaned_data["job_reference"],
                    notes=header_form.cleaned_data["notes"],
                    created_by=request.user,
                )
                for fresh_item, qty in locked_lines:
                    OrderLine.objects.create(
                        order=order, item=fresh_item, quantity_shipped=qty
                    )
                    fresh_item.quantity -= qty
                    fresh_item.save(update_fields=["quantity"])

            _send_order_email(order)
            if order.email_delivered:
                messages.success(
                    request, f"Order recorded and emailed to {order.client_email}."
                )
            else:
                messages.warning(
                    request,
                    f"Order recorded and inventory updated, but the email to "
                    f"{order.client_email} failed to send. You can resend it from "
                    "the order page.",
                )
            return redirect("inventory:order_detail", pk=order.pk)
    else:
        header_form = OrderHeaderForm()
        formset = OrderLineFormSet(prefix="line")

    return render(
        request,
        "inventory/order_form.html",
        {"header_form": header_form, "formset": formset},
    )


ORDERS_PER_PAGE = 25


def _filtered_orders(request):
    form = OrderFilterForm(request.GET)
    orders = (
        Order.objects.select_related("created_by")
        .prefetch_related("lines__item")
        .annotate(item_count=Coalesce(Sum("lines__quantity_shipped"), 0))
    )
    if form.is_valid():
        query = form.cleaned_data.get("q")
        model = form.cleaned_data.get("model")
        min_items = form.cleaned_data.get("min_items")
        max_items = form.cleaned_data.get("max_items")
        if query:
            query = query.strip()
            orders = orders.filter(
                Q(order_number__icontains=query)
                | Q(client_name__icontains=query)
                | Q(client_email__icontains=query)
                | Q(job_reference__icontains=query)
            )
        if model:
            orders = orders.filter(lines__item__model_name__icontains=model.strip()).distinct()
        if min_items is not None:
            orders = orders.filter(item_count__gte=min_items)
        if max_items is not None:
            orders = orders.filter(item_count__lte=max_items)
    return form, orders


def _order_list_context(request):
    form, orders = _filtered_orders(request)
    paginator = Paginator(orders, ORDERS_PER_PAGE)
    page_number = request.GET.get("page") or 1
    page_obj = paginator.get_page(page_number)
    return {
        "filter_form": form,
        "orders": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
    }


@login_required
def order_list(request):
    context = _order_list_context(request)
    if request.htmx:
        return render(request, "inventory/partials/order_rows.html", context)
    return render(request, "inventory/order_list.html", context)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("created_by").prefetch_related("lines__item"),
        pk=pk,
    )
    return render(request, "inventory/order_detail.html", {"order": order})


@login_required
@require_POST
def order_resend_email(request, pk):
    order = get_object_or_404(Order, pk=pk)
    _send_order_email(order)
    if order.email_delivered:
        messages.success(request, f"Resent to {order.client_email}.")
    else:
        messages.error(request, "Still failed to send -- check the email settings.")
    return redirect("inventory:order_detail", pk=order.pk)
