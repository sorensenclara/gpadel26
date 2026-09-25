from django.contrib import admin

from .models import Identidad, Jugador, Organizador


@admin.register(Identidad)
class IdentidadAdmin(admin.ModelAdmin):
    list_display = ("dni", "usuario", "ultimo_perfil", "creado")
    search_fields = ("dni", "usuario__username", "usuario__first_name", "usuario__last_name")
    list_filter = ("ultimo_perfil",)


@admin.register(Organizador)
class OrganizadorAdmin(admin.ModelAdmin):
    list_display = ("nombre_publico", "usuario", "rol", "tiene_cancha", "es_liga", "creado")
    search_fields = ("nombre_cancha", "nombre_liga", "usuario__username", "usuario__email")
    list_filter = ("rol", "tiene_cancha", "es_liga")


@admin.register(Jugador)
class JugadorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "usuario", "creado")
    search_fields = ("nombre", "usuario__username", "usuario__email")
