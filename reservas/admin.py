from django.contrib import admin

from .models import Cancha, Reserva


@admin.register(Cancha)
class CanchaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ciudad", "organizador", "precio_hora", "techada", "activa")
    list_filter = ("ciudad", "techada", "activa")
    search_fields = ("nombre", "ciudad", "organizador__nombre_publico")


@admin.register(Reserva)
class ReservaAdmin(admin.ModelAdmin):
    list_display = ("cancha", "usuario", "fecha", "hora_inicio", "estado")
    list_filter = ("estado", "fecha")
    search_fields = ("cancha__nombre", "usuario__username")
