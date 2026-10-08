from django.contrib import admin

from .models import (
    EventoAuditoria,
    Llave,
    Partido,
    PropuestaAsignacion,
    PropuestaFase,
    PropuestaZona,
    Resultado,
    Zona,
    ZonaPareja,
)


class PropuestaZonaInline(admin.TabularInline):
    model = PropuestaZona
    extra = 0


@admin.register(PropuestaFase)
class PropuestaFaseAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "cantidad_zonas", "distribucion", "total_partidos", "compatibilidad", "seleccionada_en")
    list_filter = ("categoria__torneo",)
    inlines = [PropuestaZonaInline]


class PropuestaAsignacionInline(admin.TabularInline):
    model = PropuestaAsignacion
    extra = 0
    raw_id_fields = ("inscripcion",)


@admin.register(PropuestaZona)
class PropuestaZonaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "propuesta", "orden")
    inlines = [PropuestaAsignacionInline]


class ZonaParejaInline(admin.TabularInline):
    model = ZonaPareja
    extra = 0
    raw_id_fields = ("inscripcion",)


@admin.register(Zona)
class ZonaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "orden", "creada_en")
    list_filter = ("categoria__torneo",)
    inlines = [ZonaParejaInline]


class ResultadoInline(admin.StackedInline):
    model = Resultado
    extra = 0
    raw_id_fields = ("informado_por", "disputado_por", "resuelto_por", "oficializado_por")


@admin.register(Partido)
class PartidoAdmin(admin.ModelAdmin):
    list_display = ("__str__", "categoria", "tipo", "zona", "ronda", "fecha_hora", "estado")
    list_filter = ("tipo", "estado", "categoria__torneo")
    raw_id_fields = ("pareja_a", "pareja_b", "fecha_informada_por")
    inlines = [ResultadoInline]


@admin.register(Llave)
class LlaveAdmin(admin.ModelAdmin):
    list_display = ("categoria", "generada_en")


@admin.register(EventoAuditoria)
class EventoAuditoriaAdmin(admin.ModelAdmin):
    """Solo lectura: la auditoría no se edita a mano."""

    list_display = ("creado_en", "categoria", "accion", "usuario")
    list_filter = ("accion", "categoria__torneo")
    readonly_fields = [f.name for f in EventoAuditoria._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
