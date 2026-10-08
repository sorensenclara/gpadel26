from django.contrib import admin

from .models import (
    EventoIdentidad,
    Identidad,
    Jugador,
    MembresiaOrganizador,
    Organizador,
    PermisoMembresia,
)


@admin.register(Identidad)
class IdentidadAdmin(admin.ModelAdmin):
    list_display = ("dni", "nombre_completo", "tiene_cuenta", "origen", "dni_en_revision", "creado")
    search_fields = ("dni", "dni_normalizado", "nombre", "usuario__username", "usuario__first_name", "usuario__last_name")
    list_filter = ("origen", "dni_en_revision", "ultimo_perfil")
    readonly_fields = ("dni_normalizado", "creado")
    raw_id_fields = ("usuario",)

    @admin.display(boolean=True, description="Tiene cuenta")
    def tiene_cuenta(self, obj):
        return obj.usuario_id is not None


class PermisoInline(admin.TabularInline):
    model = PermisoMembresia
    extra = 0


@admin.register(MembresiaOrganizador)
class MembresiaOrganizadorAdmin(admin.ModelAdmin):
    """Una membresía NO da permisos por sí sola: cada permiso se concede por área."""

    list_display = ("usuario", "organizador", "activa", "creada_en")
    list_filter = ("activa", "organizador")
    raw_id_fields = ("usuario", "otorgada_por")
    inlines = [PermisoInline]


@admin.register(EventoIdentidad)
class EventoIdentidadAdmin(admin.ModelAdmin):
    """Auditoría: solo lectura."""

    list_display = ("creado_en", "accion", "actor", "cuenta", "persona")
    list_filter = ("accion",)
    readonly_fields = [f.name for f in EventoIdentidad._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organizador)
class OrganizadorAdmin(admin.ModelAdmin):
    list_display = ("nombre_publico", "usuario", "rol", "tiene_cancha", "es_liga", "creado")
    search_fields = ("nombre_cancha", "nombre_liga", "usuario__username", "usuario__email")
    list_filter = ("rol", "tiene_cancha", "es_liga")


@admin.register(Jugador)
class JugadorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "usuario", "categoria_oficial", "creado")
    search_fields = ("nombre", "usuario__username", "usuario__email")
