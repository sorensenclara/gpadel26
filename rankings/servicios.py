"""
Operaciones sobre rankings: versiones inmutables y lectura de puntajes. NO incluye todavía
importaciones ni el cálculo automático de puntos.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import PuntajeRanking, TemporadaRanking, VersionRanking
from .permisos import puede_administrar


def _exigir_admin(usuario, ranking):
    if not puede_administrar(usuario, ranking):
        raise PermissionDenied("No tenés permiso para modificar este ranking.")


@transaction.atomic
def crear_version_borrador(temporada, por):
    """Abre un borrador vacío (a lo sumo uno por temporada)."""
    _exigir_admin(por, temporada.ranking)
    if temporada.estado == TemporadaRanking.ESTADO_CERRADA:
        raise ValidationError("La temporada está cerrada.")
    if temporada.versiones.filter(estado=VersionRanking.ESTADO_BORRADOR).exists():
        raise ValidationError("Ya hay un borrador abierto en esta temporada.")
    return VersionRanking.objects.create(temporada=temporada, creada_por=por)


@transaction.atomic
def nueva_version_desde(version, por, nota=""):
    """
    Corregir un ranking publicado = una versión NUEVA (borrador) copiada de la anterior. La
    versión original queda intacta para quienes ya se refirieron a ella (ej.: zonas aprobadas).
    """
    _exigir_admin(por, version.temporada.ranking)
    borrador = crear_version_borrador(version.temporada, por)
    borrador.nota = nota
    borrador.save()
    PuntajeRanking.objects.bulk_create(
        [
            PuntajeRanking(
                version=borrador, persona_id=p.persona_id, categoria=p.categoria, puntos=p.puntos, posicion=p.posicion,
                origen=p.origen, dni_original=p.dni_original, nombre_original=p.nombre_original,
                estado_vinculo=p.estado_vinculo,
            )
            for p in version.puntajes.all()
        ]
    )
    return borrador


@transaction.atomic
def publicar_version(version, por):
    """Publica el borrador. Desde ese momento es inmutable."""
    _exigir_admin(por, version.temporada.ranking)
    if version.publicada:
        raise ValidationError("Esa versión ya está publicada.")
    if not version.puntajes.exists():
        raise ValidationError("No se puede publicar una versión sin puntajes.")
    version.estado = VersionRanking.ESTADO_PUBLICADA
    version.publicada_en = timezone.now()
    version.publicada_por = por
    version.save()
    return version


def version_vigente(temporada):
    """Última versión publicada de la temporada, o None."""
    return temporada.versiones.filter(estado=VersionRanking.ESTADO_PUBLICADA).order_by("-numero").first()


def puntajes_de_pareja(version, persona_1, persona_2, categoria=""):
    """
    Puntos de cada integrante y de la pareja EN ESTA ÚNICA VERSIÓN (nunca se mezclan rankings).

    Quien no figura en la versión queda "sin ranking" (SR, `None`): nunca se le asigna 0 ni una
    posición inventada. Si alguno es SR la pareja tiene `ranking_incompleto=True` y su puntaje
    de pareja es None (no se inventa una suma parcial).
    """
    def buscar(persona):
        if persona is None:
            return None
        fila = version.puntajes.filter(persona=persona, categoria=categoria).first()
        return fila.puntos if fila is not None else None

    p1, p2 = buscar(persona_1), buscar(persona_2)
    incompleto = p1 is None or p2 is None
    return {
        "version_id": version.pk,
        "jugador_1": p1,
        "jugador_2": p2,
        "pareja": None if incompleto else p1 + p2,
        "ranking_incompleto": incompleto,
    }
