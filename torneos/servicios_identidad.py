"""
Resolución de reclamos de identidad (staff) y alta de identidad propia tras un rechazo.

Reglas:
- Verificar es una decisión EXPLÍCITA: el staff elige la identidad entre las candidatas del
  reclamo y deja método y nota. Una coincidencia de DNI o de nombre solo orienta.
- Nunca se fusionan ni se borran identidades. Solo se completa el vínculo
  `Identidad.usuario` (si estaba vacío) y las referencias derivadas de inscripciones.
- Mientras un reclamo está pendiente la participación de la cuenta queda ANCLADA a la cuenta
  (`Inscripcion.usuario`) con `persona_1` vacío; al resolver se completa o se reasigna a la
  identidad propia de la cuenta. Nunca queda atribuida a otra persona ni se pierde.
- Todo queda registrado en EventoIdentidad (quién, cuándo, qué).
"""
from django.db import transaction
from django.utils import timezone

from accounts.identidad import dar_identidad_a_cuenta, registrar_evento
from accounts.models import Identidad, ReclamoIdentidad

from .models import Inscripcion


class ReclamoInvalido(ValueError):
    """La operación sobre el reclamo no es válida (estado, identidad, datos faltantes)."""


def _completar_inscripciones_de_la_cuenta(usuario, persona):
    """Asocia a `persona` las inscripciones hechas por la cuenta que quedaron sin persona_1."""
    return Inscripcion.objects.filter(usuario=usuario, persona_1__isnull=True).exclude(persona_2=persona).update(
        persona_1=persona
    )


@transaction.atomic
def verificar_reclamo(reclamo, persona, por, metodo, nota):
    """
    El staff verificó (fuera del sistema o con la documentación) que la cuenta ES esa
    identidad. `persona` debe ser una de las candidatas y no tener cuenta.
    """
    if reclamo.estado != ReclamoIdentidad.ESTADO_PENDIENTE:
        raise ReclamoInvalido("Solo se puede verificar un reclamo pendiente.")
    if metodo not in dict(ReclamoIdentidad.METODO_CHOICES):
        raise ReclamoInvalido("Indicá el método con el que se verificó la identidad.")
    if not (nota or "").strip():
        raise ReclamoInvalido("Dejá constancia de cómo se verificó la identidad.")
    if persona is None or not reclamo.candidatas.filter(pk=persona.pk).exists():
        raise ReclamoInvalido("La identidad tiene que ser una de las candidatas del reclamo.")
    persona = Identidad.objects.select_for_update().get(pk=persona.pk)
    if persona.usuario_id is not None:
        raise ReclamoInvalido("Esa identidad ya tiene una cuenta vinculada.")
    if Identidad.objects.filter(usuario=reclamo.usuario).exists():
        raise ReclamoInvalido("La cuenta ya tiene una identidad propia.")

    persona.usuario = reclamo.usuario
    if not persona.localidad:
        persona.localidad = reclamo.localidad_declarada
    persona.save()

    reclamo.persona = persona
    reclamo.estado = ReclamoIdentidad.ESTADO_VERIFICADO
    reclamo.metodo_verificacion = metodo
    reclamo.nota_resolucion = nota
    reclamo.resuelto_por = por
    reclamo.resuelto_en = timezone.now()
    reclamo.save()

    propias = _completar_inscripciones_de_la_cuenta(reclamo.usuario, persona)
    como_companero = Inscripcion.objects.filter(persona_2=persona, usuario_2__isnull=True).exclude(
        usuario=reclamo.usuario
    ).update(usuario_2=reclamo.usuario)

    registrar_evento(
        "reclamo_verificado", actor=por, cuenta=reclamo.usuario, persona=persona, reclamo=reclamo,
        metodo=metodo, nota=nota, inscripciones_propias_completadas=propias,
        inscripciones_como_companero_vinculadas=como_companero,
    )

    # Otros reclamos abiertos sobre la misma identidad: NO se rechazan solos; quedan en conflicto
    # para que el staff los resuelva (rechazándolos con su nota).
    otros = ReclamoIdentidad.objects.filter(
        estado=ReclamoIdentidad.ESTADO_PENDIENTE, candidatas=persona
    ).exclude(pk=reclamo.pk)
    for otro in otros:
        otro.estado = ReclamoIdentidad.ESTADO_EN_CONFLICTO
        otro.save(update_fields=["estado"])
        registrar_evento("reclamo_en_conflicto", actor=por, cuenta=otro.usuario, persona=persona, reclamo=otro)
    return reclamo


@transaction.atomic
def rechazar_reclamo(reclamo, por, nota):
    """El staff determinó que la cuenta NO es esa identidad. No cambia ninguna identidad."""
    if not reclamo.abierto:
        raise ReclamoInvalido("Ese reclamo ya fue resuelto.")
    if not (nota or "").strip():
        raise ReclamoInvalido("Dejá constancia del motivo del rechazo.")
    reclamo.estado = ReclamoIdentidad.ESTADO_RECHAZADO
    reclamo.nota_resolucion = nota
    reclamo.resuelto_por = por
    reclamo.resuelto_en = timezone.now()
    reclamo.save()
    registrar_evento("reclamo_rechazado", actor=por, cuenta=reclamo.usuario, reclamo=reclamo, nota=nota)
    return reclamo


@transaction.atomic
def completar_identidad_propia(usuario, dni, localidad="", ultimo_perfil=""):
    """
    Para una cuenta cuyo reclamo fue rechazado (o que todavía no tiene identidad): crea su
    identidad propia con el DNI que corresponde. Si ese DNI también pertenece a una identidad
    sin cuenta, vuelve a abrir un reclamo (nunca se apropia sola). Al crearse la identidad
    propia, se completan `persona_1` de las inscripciones que la cuenta hizo mientras tanto.
    Devuelve (identidad, reclamo).
    """
    if Identidad.objects.filter(usuario=usuario).exists():
        raise ReclamoInvalido("La cuenta ya tiene una identidad.")
    if usuario.reclamos_identidad.filter(estado__in=ReclamoIdentidad.ESTADOS_ABIERTOS).exists():
        raise ReclamoInvalido("La cuenta tiene un reclamo abierto: resolvelo primero.")
    identidad, reclamo = dar_identidad_a_cuenta(usuario, dni, localidad, ultimo_perfil)
    if identidad is not None:
        completadas = _completar_inscripciones_de_la_cuenta(usuario, identidad)
        registrar_evento("identidad_propia_creada", cuenta=usuario, persona=identidad, inscripciones_completadas=completadas)
    return identidad, reclamo
