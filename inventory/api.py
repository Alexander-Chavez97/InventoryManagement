from rest_framework import viewsets

from .models import Item
from .serializers import ItemSerializer


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
