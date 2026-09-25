from django.urls import path

from . import views

app_name = "torneos"

urlpatterns = [
    path("", views.mis_torneos, name="mis_torneos"),
    path("nuevo/", views.crear_torneo, name="crear_torneo"),
    path("<slug:codigo>/", views.organizador_detalle, name="organizador_detalle"),
    path("<slug:codigo>/editar/", views.editar_torneo, name="editar_torneo"),
    path("<slug:codigo>/estado/", views.cambiar_estado_torneo, name="cambiar_estado"),
    path("<slug:codigo>/eliminar/", views.eliminar_torneo, name="eliminar_torneo"),
    path("<slug:codigo>/categorias/nueva/", views.agregar_categoria, name="agregar_categoria"),
    path("<slug:codigo>/categorias/<int:categoria_id>/editar/", views.editar_categoria, name="editar_categoria"),
    path("<slug:codigo>/categorias/<int:categoria_id>/eliminar/", views.eliminar_categoria, name="eliminar_categoria"),
]
