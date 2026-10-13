from datetime import timedelta

from django.utils import timezone
from rest_framework import viewsets

from .models import Item, Order
from .serializers import ItemSerializer, OrderSerializer


class ItemViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only: GET /api/items/ (list) and GET /api/items/<pk>/ (detail)."""

    serializer_class = ItemSerializer
    queryset = (
        Item.objects.select_related("location", "subsubcategory__subcategory__category")
        .prefetch_related("photos")
        .all()
    )

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.query_params.get("status")
        item_type = self.request.query_params.get("item_type")
        category = self.request.query_params.get("category")
        q = self.request.query_params.get("q")
        if status:
            qs = qs.filter(status=status)
        if item_type:
            qs = qs.filter(item_type=item_type)
        if category:
            qs = qs.filter(subsubcategory__subcategory__category__name__iexact=category)
        if q:
            qs = qs.filter(model_name__icontains=q)
        return qs


DEFAULT_ORDER_HISTORY_DAYS = 90


class OrderViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only: GET /api/orders/ (list) and GET /api/orders/<pk>/ (detail).

    Backs the agent's order-history lookups -- no write methods are wired
    up, matching the read-only scope of the whole MCP integration (see the
    Buildathon Inventory Agent brief, Section 7).
    """

    serializer_class = OrderSerializer
    queryset = Order.objects.prefetch_related("lines__item").all()

    def get_queryset(self):
        qs = super().get_queryset()
        item_query = self.request.query_params.get("item")
        days_param = self.request.query_params.get("days")
        if item_query:
            qs = qs.filter(lines__item__model_name__icontains=item_query).distinct()

        # days=0 (or "all") means no date bound -- full history. Omitting
        # the param at all defaults to the last 90 days, so a plain
        # `GET /api/orders/` doesn't silently return every order ever
        # placed; callers that actually want everything ask for it
        # explicitly.
        if days_param in ("0", "all"):
            days = None
        elif days_param:
            days = int(days_param)
        else:
            days = DEFAULT_ORDER_HISTORY_DAYS
        if days is not None:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(created_at__gte=cutoff)
        return qs
