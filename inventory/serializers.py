from rest_framework import serializers

from .models import Item, ItemPhoto, Location


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ["id", "building", "aisle", "shelf", "bin"]


class ItemPhotoSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = ItemPhoto
        fields = ["id", "image", "kind", "uploaded_at"]

    def get_image(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class ItemSerializer(serializers.ModelSerializer):
    location = LocationSerializer(read_only=True)
    photos = ItemPhotoSerializer(many=True, read_only=True)
    item_type_display = serializers.CharField(source="get_item_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Item
        fields = [
            "id",
            "serial_number",
            "item_type",
            "item_type_display",
            "status",
            "status_display",
            "location",
            "notes",
            "created_at",
            "updated_at",
            "photos",
        ]