"""
Servicios de identidad que dependen solo de `accounts` (sin tocar torneos).

- Alta de una cuenta con su identidad, o con un RECLAMO si el DNI ya pertenece a una
  identidad existente sin cuenta (nunca se apropia de ella por coincidencia de DNI).
- Identidades sin cuenta (personas que participan porque las inscribió un compañero).
- Registro de auditoría (EventoIdentidad).
"""
from django.db import IntegrityError, transaction

from .models import (
    EventoIdentidad,
    Identidad,
    ReclamoIdentidad,
    nombres_compatibles,
    normalizar_dni,
)


class DniYaRegistrado(Exception):
    """El DNI ya pertenece a una CUENTA: no se puede registrar otra con él."""


def registrar_evento(accion, *, actor=None, cuenta=None, persona=None, reclamo=None, **detalle):
    return EventoIdentidad.objects.create(
        accion=accion, actor=actor, cuenta=cuenta, persona=persona, reclamo=reclamo, detalle=detalle
    )


def candidatas_por_dni(dni):
    """Identidades cuyo DNI normalizado coincide (incluye las marcadas en revisión)."""
    buscado = normalizar_dni(dni)
    if not buscado:
        return []
    return list(Identidad.objects.filter(dni_normalizado=buscado).select_related("usuario").order_by("pk"))


def nombre_para_comparar(identidad):
    """Nombre real conocido de la identidad ('' si no hay datos): nunca el username."""
    if identidad.usuario_id:
        nombre = identidad.usuario.get_full_name().strip()
        if nombre:
            return nombre
        jugador = getattr(identidad.usuario, "jugador", None)
        return (jugador.nombre if jugador is not None else "") or ""
    return identidad.nombre


def crear_persona_sin_cuenta(dni, nombre, localidad="", origen=Identidad.ORIGEN_INSCRIPCION):
    """
    Crea una identidad sin cuenta para el DNI dado, o devuelve (sin duplicar) la que ya
    exista si otra petición se adelantó. Si hay ambigüedad (varias / en revisión) devuelve None.
    """
    nombre = (nombre or "").strip()
    if not nombre or not normalizar_dni(dni):
        return None
    try:
        with transaction.atomic():
            persona = Identidad.objects.create(
                usuario=None, dni=dni.strip(), nombre=nombre, localidad=localidad or "", origen=origen
            )
        registrar_evento("persona_creada", persona=persona, origen=origen)
        return persona
    except IntegrityError:
        # Carrera (o el DNI exacto ya existe): nunca se crea un duplicado.
        candidatas = candidatas_por_dni(dni)
        return candidatas[0] if len(candidatas) == 1 and not candidatas[0].dni_en_revision else None


@transaction.atomic
def dar_identidad_a_cuenta(usuario, dni, localidad="", ultimo_perfil=""):
    """
    Da identidad a una cuenta recién creada. Devuelve (identidad, reclamo):

    - DNI libre            -> (Identidad nueva de la cuenta, None).
    - DNI de una cuenta    -> DniYaRegistrado (el formulario ya lo rechaza antes).
    - DNI de una identidad -> (None, ReclamoIdentidad pendiente). La cuenta NO queda
      vinculada y no ve nada del historial hasta que el staff verifique su identidad.
      Si hay varias identidades candidatas o el DNI está en revisión, el reclamo queda
      sin persona elegida: la decide el staff.
    """
    candidatas = candidatas_por_dni(dni)
    if any(c.usuario_id for c in candidatas):
        raise DniYaRegistrado(dni)

    if not candidatas:
        try:
            with transaction.atomic():
                identidad = Identidad.objects.create(
                    usuario=usuario, dni=dni.strip(), localidad=localidad or "", ultimo_perfil=ultimo_perfil
                )
        except IntegrityError:
            candidatas = candidatas_por_dni(dni)  # se adelantó otra petición: se trata como reclamo
        else:
            registrar_evento("cuenta_con_identidad", cuenta=usuario, persona=identidad)
            return identidad, None

    unica = candidatas[0] if len(candidatas) == 1 and not candidatas[0].dni_en_revision else None
    nombre_declarado = usuario.get_full_name().strip()
    reclamo = ReclamoIdentidad.objects.create(
        usuario=usuario,
        persona=unica,
        dni_declarado=dni.strip(),
        nombre_declarado=nombre_declarado,
        localidad_declarada=localidad or "",
        indicios={
            "dni_coincide": True,
            "cantidad_candidatas": len(candidatas),
            "dni_en_revision": any(c.dni_en_revision for c in candidatas),
            "nombre_compatible": nombres_compatibles(nombre_declarado, nombre_para_comparar(unica)) if unica else None,
            "participaciones_de_la_identidad": (
                unica.inscripciones_como_jugador_1.count() + unica.inscripciones_como_jugador_2.count()
                if unica else None
            ),
        },
    )
    reclamo.candidatas.set(candidatas)
    registrar_evento("reclamo_creado", cuenta=usuario, persona=unica, reclamo=reclamo, dni_normalizado=normalizar_dni(dni))
    return None, reclamo
