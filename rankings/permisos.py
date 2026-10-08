"""
Quién puede qué sobre un ranking. Los permisos de rankings son SEPARADOS de los de torneos y
canchas: pertenecer a una entidad no da, por sí solo, permiso sobre sus rankings; hace falta una
fila de PermisoMembresia explícita en el área "rankings" (ver accounts.models).

                      ver      usar en torneos   modificar
  ranking público     todos    organizadores     solo administradores autorizados de la entidad
  ranking privado     entidad  entidad           solo administradores autorizados de la entidad
  ranking general     todos    organizadores     solo el staff de GPADEL

El staff puede VER un ranking privado (soporte), pero no modificar uno externo: eso es de la entidad.
"""
from accounts.models import PermisoMembresia, tiene_permiso

from .models import Ranking

AREA = PermisoMembresia.AREA_RANKINGS


def _autenticado(usuario):
    return usuario is not None and getattr(usuario, "is_authenticated", False)


def _es_staff(usuario):
    return _autenticado(usuario) and (usuario.is_staff or usuario.is_superuser)


def puede_ver(usuario, ranking):
    if ranking.visibilidad == Ranking.VISIBILIDAD_PUBLICO:
        return True
    return _es_staff(usuario) or tiene_permiso(usuario, ranking.entidad, AREA, PermisoMembresia.NIVEL_USAR)


def puede_usar(usuario, ranking):
    """¿Puede elegir este ranking para priorizar zonas en un torneo?"""
    if not _autenticado(usuario):
        return False
    # Quien tiene permiso de rankings en la entidad propietaria lo puede usar siempre.
    if ranking.entidad_id and tiene_permiso(usuario, ranking.entidad, AREA, PermisoMembresia.NIVEL_USAR):
        return True
    if ranking.visibilidad == Ranking.VISIBILIDAD_PUBLICO:
        # Público: lo pueden usar otros organizadores (perfil propio o permiso explícito de torneos).
        return hasattr(usuario, "organizador") or usuario.membresias_organizador.filter(
            activa=True, permisos__area=PermisoMembresia.AREA_TORNEOS
        ).exists()
    return False


def puede_usar_en_torneo(usuario, ranking, torneo):
    """Además de poder usar el ranking, hay que poder gestionar ESE torneo (regla vigente:
    ser el organizador del torneo o tener permiso de torneos en su entidad)."""
    if not puede_usar(usuario, ranking):
        return False
    organizador = getattr(usuario, "organizador", None)
    if organizador is not None and organizador.pk == torneo.organizador_id:
        return True
    return tiene_permiso(usuario, torneo.organizador, PermisoMembresia.AREA_TORNEOS, PermisoMembresia.NIVEL_USAR)


def puede_administrar(usuario, ranking):
    if not _autenticado(usuario):
        return False
    if ranking.tipo == Ranking.TIPO_GENERAL:
        return _es_staff(usuario)
    return tiene_permiso(usuario, ranking.entidad, AREA, PermisoMembresia.NIVEL_ADMINISTRAR)


def rankings_visibles_para(usuario):
    """Rankings que esta cuenta puede ver (para listados)."""
    visibles = [r for r in Ranking.objects.select_related("entidad") if puede_ver(usuario, r)]
    return visibles
