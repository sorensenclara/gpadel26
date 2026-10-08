from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import PuntajeRanking, Ranking, ReglaPuntaje, TemporadaRanking, TorneoPuntuable, VersionRanking
from .permisos import puede_administrar


class _SoloQuienAdministra(admin.ModelAdmin):
    """Modificar un ranking es solo de los administradores autorizados de su entidad
    (o del staff para el general). Los demás lo pueden ver, no modificar."""

    def _ranking_de(self, obj):
        return obj

    def has_change_permission(self, request, obj=None):
        if obj is None:
            return super().has_change_permission(request, obj)
        return super().has_change_permission(request, obj) and puede_administrar(request.user, self._ranking_de(obj))

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return super().has_delete_permission(request, obj)
        return super().has_delete_permission(request, obj) and puede_administrar(request.user, self._ranking_de(obj))


@admin.register(Ranking)
class RankingAdmin(_SoloQuienAdministra):
    list_display = ("nombre", "tipo", "entidad", "visibilidad", "por_categoria")
    list_filter = ("tipo", "visibilidad", "por_categoria")
    search_fields = ("nombre", "ambito", "entidad__nombre_liga", "entidad__nombre_cancha")

    def save_model(self, request, obj, form, change):
        if not change and not puede_administrar(request.user, obj):
            raise PermissionDenied("No tenés permiso para crear rankings de esa entidad.")
        if not change and not obj.creado_por_id:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(TemporadaRanking)
class TemporadaRankingAdmin(_SoloQuienAdministra):
    list_display = ("__str__", "estado", "fecha_desde", "fecha_hasta")
    list_filter = ("estado", "ranking")

    def _ranking_de(self, obj):
        return obj.ranking


class PuntajeInline(admin.TabularInline):
    model = PuntajeRanking
    extra = 0
    raw_id_fields = ("persona",)

    def _publicada(self, obj):
        return obj is not None and obj.publicada

    def has_add_permission(self, request, obj=None):
        return not self._publicada(obj)

    def has_change_permission(self, request, obj=None):
        return not self._publicada(obj)

    def has_delete_permission(self, request, obj=None):
        return not self._publicada(obj)


@admin.register(VersionRanking)
class VersionRankingAdmin(_SoloQuienAdministra):
    list_display = ("__str__", "estado", "creada_en", "publicada_en")
    list_filter = ("estado", "temporada__ranking")
    readonly_fields = ("numero", "creada_en", "publicada_en", "publicada_por")
    inlines = [PuntajeInline]

    def _ranking_de(self, obj):
        return obj.temporada.ranking

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.publicada:
            base += ["temporada", "estado", "nota", "creada_por"]  # inmutable una vez publicada
        return base


@admin.register(ReglaPuntaje)
class ReglaPuntajeAdmin(admin.ModelAdmin):
    list_display = ("nombre", "vigente", "creada_en")


@admin.register(TorneoPuntuable)
class TorneoPuntuableAdmin(admin.ModelAdmin):
    list_display = ("torneo", "temporada", "regla", "puntuable", "decidido_por", "decidido_en")
    list_filter = ("puntuable",)
