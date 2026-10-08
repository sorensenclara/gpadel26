"""
Reglas de PARTICIPACIÓN: quién puede inscribirse a qué categoría de un torneo.

Para cada integrante de la pareja que se inscribe (jugador 1 y compañero):
- Ya inscripto en ESA categoría               -> no puede volver a inscribirse (nunca).
- Ya inscripto en OTRA categoría del torneo   -> solo si el organizador habilitó
  `Torneo.permite_multiples_categorias`, la nueva categoría es MÁS ALTA que la anterior y la
  pareja es DISTINTA (nunca la misma pareja en dos categorías).
Las inscripciones rechazadas no cuentan.

Para saber si "es la misma persona" se usan la cuenta, la identidad y el DNI normalizado. Es una
RESTRICCIÓN (anti-abuso), así que una coincidencia alcanza para aplicarla; nunca se usa para
atribuir historial.
"""
from dataclasses import dataclass

from django.db.models import Q

from accounts.models import normalizar_dni

from .models import Inscripcion, Torneo


@dataclass
class Participante:
    nombre: str = ""
    usuario_id: int | None = None
    persona_id: int | None = None
    dnis: frozenset = frozenset()
    es_el_usuario: bool = False


def _datos_del_lado(insc, n):
    usuario_id = insc.usuario_id if n == 1 else insc.usuario_2_id
    return usuario_id, getattr(insc, f"persona_{n}_id"), normalizar_dni(getattr(insc, f"dni_{n}"))


def es_el_mismo(participante, insc, n):
    usuario_id, persona_id, dni = _datos_del_lado(insc, n)
    if participante.usuario_id and participante.usuario_id == usuario_id:
        return True
    if participante.persona_id and participante.persona_id == persona_id:
        return True
    return bool(dni) and dni in participante.dnis


def lado_de(participante, insc):
    """1 o 2 según en qué lado de esa inscripción figura el participante; None si no figura."""
    for n in (1, 2):
        if es_el_mismo(participante, insc, n):
            return n
    return None


def inscripciones_activas(torneo):
    return list(torneo.inscripciones.exclude(estado__in=Inscripcion.ESTADOS_NO_VIGENTES).select_related("categoria"))


def es_mas_alta(torneo, nueva, previa):
    """Número menor = categoría superior. Solo tiene sentido en torneos por categoría."""
    if torneo.modalidad_categoria != Torneo.MODALIDAD_POR_CATEGORIA:
        return False
    try:
        return nueva.nivel_numerico < previa.nivel_numerico
    except (ValueError, IndexError):
        return False


def _quien(p):
    return "Vos" if p.es_el_usuario else (p.nombre or "El jugador")


def _ya_esta(p):
    return "Ya estás" if p.es_el_usuario else f"{p.nombre or 'El jugador'} ya está"


def validar_participacion(torneo, categoria, participantes, previas=None):
    """Lista de mensajes de error (vacía si la pareja puede inscribirse en `categoria`)."""
    previas = inscripciones_activas(torneo) if previas is None else previas
    errores = []
    for i, p in enumerate(participantes):
        socio = participantes[1 - i]
        for e in previas:
            n = lado_de(p, e)
            if n is None:
                continue
            otro = 2 if n == 1 else 1
            donde = e.categoria.nombre
            if e.categoria_id == categoria.id:
                errores.append(f"{_ya_esta(p)} inscripto en {categoria.nombre}.")
            elif not torneo.permite_multiples_categorias:
                errores.append(
                    f"{_ya_esta(p)} inscripto en {donde}. El organizador no habilitó jugar más de una "
                    "categoría en este torneo."
                )
            elif es_el_mismo(socio, e, otro):
                errores.append(
                    f"{_quien(p)} y {_quien(socio)} ya forman pareja en {donde}: en otra categoría "
                    "tienen que jugar con otra pareja."
                )
            elif not es_mas_alta(torneo, categoria, e.categoria):
                errores.append(f"{_ya_esta(p)} inscripto en {donde}: solo se pueden sumar categorías más altas.")
    return list(dict.fromkeys(errores))


def categorias_disponibles(torneo, previas_del_usuario):
    """Categorías en las que esta persona todavía podría inscribirse (la validación final la hace
    validar_participacion, que además mira a la pareja)."""
    todas = list(torneo.categorias.all())
    if not previas_del_usuario:
        return todas
    if not torneo.permite_multiples_categorias:
        return []
    return [c for c in todas if all(es_mas_alta(torneo, c, e.categoria) for e in previas_del_usuario)]


def inscripciones_de(usuario, incluir_rechazadas=False):
    """Todas las inscripciones donde figura esta cuenta: las que hizo ella y las que le hizo su
    pareja. Se llega por la cuenta vinculada o por una identidad VERIFICADA de la cuenta."""
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        return Inscripcion.objects.none()
    qs = Inscripcion.objects.filter(
        Q(usuario=usuario) | Q(usuario_2=usuario) | Q(persona_1__usuario=usuario) | Q(persona_2__usuario=usuario)
    ).distinct()
    return qs if incluir_rechazadas else qs.exclude(estado__in=Inscripcion.ESTADOS_NO_VIGENTES)


def lado_del_usuario(insc, usuario):
    """1 si la cuenta es el jugador 1 de esa inscripción, 2 si es el compañero."""
    p1 = insc.persona_1
    return 1 if (insc.usuario_id == usuario.pk or (p1 is not None and p1.usuario_id == usuario.pk)) else 2


def estado_por_torneo(usuario, torneos):
    """{torneo.id: {"categorias": [...], "puede_otra": bool}} para marcar INSCRIPTO en los listados."""
    if not getattr(usuario, "is_authenticated", False):
        return {}
    por_torneo = {}
    for insc in inscripciones_de(usuario).select_related("categoria"):
        por_torneo.setdefault(insc.torneo_id, []).append(insc)
    resultado = {}
    for torneo in torneos:
        previas = por_torneo.get(torneo.pk)
        if previas:
            resultado[torneo.pk] = {
                "categorias": [e.categoria.nombre for e in previas],
                "puede_otra": bool(categorias_disponibles(torneo, previas)),
            }
    return resultado
