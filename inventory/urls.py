from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("", views.item_list, name="item_list"),
    path("intake/", views.scanner_intake, name="intake"),
    path("intake/subcategories/", views.subcategory_options, name="subcategory_options"),
    path("intake/subsubcategories/", views.subsubcategory_options, name="subsubcategory_options"),
    path("items/<int:pk>/", views.item_detail, name="item_detail"),
    path("items/<int:pk>/status/", views.quick_status, name="quick_status"),
    path("staff/new/", views.create_staff_user, name="create_staff"),
    path("shipments/", views.shipment_list, name="shipment_list"),
    path("shipments/new/", views.shipment_new, name="shipment_new"),
    path("shipments/<int:pk>/", views.shipment_detail, name="shipment_detail"),
    path("shipments/<int:pk>/resend-email/", views.shipment_resend_email, name="shipment_resend_email"),
]
