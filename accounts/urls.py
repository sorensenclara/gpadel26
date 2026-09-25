from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("mi-cuenta/", views.mi_cuenta, name="mi_cuenta"),
    path("cambiar-perfil/<str:perfil>/", views.cambiar_perfil, name="cambiar_perfil"),
]
