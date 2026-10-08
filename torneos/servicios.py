"""
Lógica de dominio de torneos que no pertenece a una vista ni a un modelo.

- Identificación del compañero (Jugador 2) por DNI, con vinculación a su cuenta.
- Casos ambiguos (un DNI que coincide con varias cuentas): jamás se vincula ni
  se usa la categoría de ninguna; se deja un caso abierto para verificación.
- Categorías DECLARADAS por terceros (no oficiales) y su piso.
- Disponibilidad estructurada a partir del texto que arma el widget del form.

Regla de oro de la vinculación: SOLO se completa `Inscripcion.usuario_2`.
Nunca se tocan `categoria_oficial_1/2`, `nombre_2`, `dni_2` ni ningún otro
dato declarado en la inscripción: son historia de ese torneo y no deben
cambiar si el jugador después cambia su categoría oficial o su nombre.

Regla de oro de la categoría: la categoría OFICIAL (Jugador.categoria_oficial)
solo la define su titular. Lo que un tercero declara para un compañero queda
únicamente en la inscripción, como dato histórico, hasta que el titular lo confirme.
"""
import re

from django.db import transaction
from django.db.models import Min
from django.utils import timezone

from accounts.identidad import (
    candidatas_por_dni,
    crear_persona_sin_cuenta,
    nombre_para_comparar,
    registrar_evento,
)
from accounts.models import (  # noqa: F401  (re-exports: API pública de este módulo)
    Identidad,
    nombres_compatibles,
    normalizar_dni,
    puede_subir_categoria,
)

from .models import DisponibilidadInscripcion, Inscripcion, RevisionIdentidad


# ---------------------------------------------------------------------------
# Identidad por DNI
# ---------------------------------------------------------------------------
LARGO_DNI_RAZONABLE = range(6, 10)  # 6 a 9 dígitos


def identidades_por_dni(dni):
    """Todas las Identidad cuyo DNI normalizado coincide (0, 1 o más)."""
    return candidatas_por_dni(dni)


def _identidad_unica_por_dni(dni):
    """La Identidad si hay UNA sola y no está en revisión; si no, None (ante la duda no se vincula)."""
    coincidencias = identidades_por_dni(dni)
    if len(coincidencias) == 1 and not coincidencias[0].dni_en_revision:
        return coincidencias[0]
    return None


def registrar_revision_dni(dni, cuentas, inscripciones=(), motivo=RevisionIdentidad.MOTIVO_DNI_AMBIGUO):
    """
    Deja marcado (o amplía) el caso abierto para ese DNI y motivo. Es idempotente: un
    solo caso abierto por DNI y motivo, al que se le suman cuentas e inscripciones.
    """
    buscado = normalizar_dni(dni)
    if not buscado:
        return None
    with transaction.atomic():
        revision, _ = RevisionIdentidad.objects.get_or_create(
            dni=buscado, motivo=motivo, estado=RevisionIdentidad.ESTADO_ABIERTA
        )
        revision.cuentas.add(*[c for c in cuentas if c is not None])
        if inscripciones:
            revision.inscripciones.add(*inscripciones)
    return revision


def resolver_revision(revision, titular, por, nota=""):
    """
    Cierra un caso DESPUÉS de verificar la identidad real de la persona.

    `titular`: la cuenta (de las que figuran en el caso) que es la verdadera titular, o
    None si ninguna corresponde. Con titular, se le vinculan las inscripciones históricas
    que la nombraban por DNI y no tenían cuenta vinculada (solo `usuario_2` y `persona_2`;
    jamás las categorías declaradas). Deja constancia en la auditoría.

    No corrige los DNI de las otras identidades ni fusiona nada: eso es una corrección de
    datos aparte. Devuelve cuántas inscripciones se vincularon.
    """
    if revision.estado != RevisionIdentidad.ESTADO_ABIERTA:
        raise ValueError("Ese caso ya fue resuelto.")
    if titular is not None and not revision.cuentas.filter(pk=titular.pk).exists():
        raise ValueError("La cuenta indicada no es una de las que figuran en ese caso.")

    vinculadas = 0
    with transaction.atomic():
        if titular is not None:
            identidad = getattr(titular, "identidad", None)
            pendientes = Inscripcion.objects.filter(usuario_2__isnull=True).exclude(usuario=titular)
            ids = [
                pk for pk, dni_2 in pendientes.values_list("pk", "dni_2")
                if normalizar_dni(dni_2) == revision.dni
            ]
            vinculadas = Inscripcion.objects.filter(pk__in=ids).update(usuario_2=titular)
            if identidad is not None:
                Inscripcion.objects.filter(pk__in=ids, persona_2__isnull=True).exclude(
                    persona_1=identidad
                ).update(persona_2=identidad)
        revision.estado = RevisionIdentidad.ESTADO_RESUELTA
        revision.titular_verificado = titular
        revision.nota_resolucion = nota
        revision.resuelta_por = por
        revision.resuelta_en = timezone.now()
        revision.save()
        registrar_evento(
            "revision_resuelta", actor=por, cuenta=titular, persona=getattr(titular, "identidad", None),
            dni=revision.dni, motivo=revision.motivo, inscripciones_vinculadas=vinculadas, nota=nota,
        )
    return vinculadas


class Companero:
    """
    Qué sabemos del compañero a partir del DNI que ingresó quien inscribe.

    - `persona`: la identidad deportiva con ese DNI (con o sin cuenta), si hay UNA.
    - `usuario`: su cuenta, si la identidad tiene una.
    - `jugador`: su perfil de Jugador, si tiene (es donde vive la categoría).
    - `categoria`: su categoría OFICIAL registrada, o None.
    - `ambiguo`: el DNI coincide con varias identidades o está en revisión -> NO hay
      identidad utilizable (no se vincula ni se usa la categoría de ninguna).
    """

    def __init__(self, usuario=None, jugador=None, ambiguo=False, cuentas=(), persona=None):
        self.usuario = usuario
        self.jugador = jugador
        self.ambiguo = ambiguo
        self.cuentas = list(cuentas)
        self.persona = persona

    @property
    def tiene_cuenta(self):
        return self.usuario is not None

    @property
    def categoria(self):
        return self.jugador.categoria_oficial if self.jugador is not None else None

    @property
    def categoria_bloqueada(self):
        """True si ya tiene categoría oficial: quien inscribe NO puede cambiarla."""
        return self.categoria is not None

    @property
    def categoria_minima(self):
        """Mejor categoría ya declarada (y validada por el organizador) para
        esta cuenta, o None. Solo aplica si tiene cuenta sin categoría oficial."""
        if self.tiene_cuenta and not self.categoria_bloqueada:
            return categoria_minima_declarada(self.usuario)
        return None


def buscar_companero(dni):
    coincidencias = identidades_por_dni(dni)
    if len(coincidencias) > 1 or any(c.dni_en_revision for c in coincidencias):
        return Companero(ambiguo=True, cuentas=[c.usuario for c in coincidencias if c.usuario_id])
    if not coincidencias:
        return Companero()
    persona = coincidencias[0]
    usuario = persona.usuario
    return Companero(
        usuario=usuario, jugador=getattr(usuario, "jugador", None) if usuario else None, persona=persona
    )


def resolver_persona(dni, nombre, localidad="", inscripcion=None):
    """
    Identidad de un compañero a partir del DNI y nombre declarados en una inscripción.
    Devuelve la Identidad o None. NUNCA crea una identidad duplicada ante una coincidencia
    dudosa, y nunca vincula por una coincidencia de nombre/DNI sin más:

    - DNI inutilizable (vacío o de largo imposible)         -> None.
    - Ninguna identidad con ese DNI                         -> se crea una SIN cuenta.
    - Una sola, nombre compatible o sin datos para comparar -> esa.
    - Una sola pero el nombre no se parece                  -> None + caso de revisión
                                                              (posible error de tipeo).
    - Varias, o el DNI está en revisión                     -> None + caso de revisión.
    """
    if len(normalizar_dni(dni)) not in LARGO_DNI_RAZONABLE:
        return None
    coincidencias = identidades_por_dni(dni)
    afectadas = [inscripcion] if inscripcion is not None else []

    if not coincidencias:
        return crear_persona_sin_cuenta(dni, nombre, localidad)
    if len(coincidencias) > 1 or coincidencias[0].dni_en_revision:
        registrar_revision_dni(dni, [c.usuario for c in coincidencias if c.usuario_id], afectadas)
        return None
    persona = coincidencias[0]
    if nombres_compatibles(nombre, nombre_para_comparar(persona)) is False:
        registrar_revision_dni(
            dni, [persona.usuario] if persona.usuario_id else [], afectadas,
            motivo=RevisionIdentidad.MOTIVO_NOMBRE_NO_COINCIDE,
        )
        return None
    return persona


def asignar_personas(inscripcion):
    """
    Completa `persona_1` / `persona_2` de una inscripción (y `usuario_2` cuando el
    compañero tiene cuenta). Devuelve True si quedó vinculada una CUENTA como compañero.

    - Jugador 1: sale de su cuenta, nunca del DNI tipeado. Si su cuenta tiene un reclamo
      de identidad pendiente (no tiene identidad todavía) queda vacío: la participación
      queda anclada a la cuenta y se completa al resolver el reclamo.
    - Jugador 2: ver `resolver_persona`.
    Solo escribe estos campos: no toca ningún dato declarado.
    """
    cambios = []
    if inscripcion.usuario_id and not inscripcion.persona_1_id:
        propia = Identidad.objects.filter(usuario_id=inscripcion.usuario_id).first()
        if propia is not None:
            inscripcion.persona_1 = propia
            cambios.append("persona_1")

    vinculo_cuenta = False
    persona_2 = None
    if inscripcion.persona_2_id:
        persona_2 = inscripcion.persona_2
    elif inscripcion.usuario_2_id:
        persona_2 = Identidad.objects.filter(usuario_id=inscripcion.usuario_2_id).first()
    else:
        persona_2 = resolver_persona(inscripcion.dni_2, inscripcion.nombre_completo_2, inscripcion.localidad_2, inscripcion)

    if persona_2 is not None and persona_2.pk != inscripcion.persona_1_id:
        if not inscripcion.persona_2_id:
            inscripcion.persona_2 = persona_2
            cambios.append("persona_2")
        if persona_2.usuario_id and not inscripcion.usuario_2_id and persona_2.usuario_id != inscripcion.usuario_id:
            inscripcion.usuario_2_id = persona_2.usuario_id
            cambios.append("usuario_2")
            vinculo_cuenta = True
    if cambios:
        inscripcion.save(update_fields=cambios)
    return vinculo_cuenta


def vincular_companero(inscripcion):
    """
    Al guardar una inscripción: identifica al compañero (ver `asignar_personas`). Devuelve
    True si vinculó una CUENTA existente como compañero (`usuario_2`).
    """
    return asignar_personas(inscripcion)


# ---------------------------------------------------------------------------
# Categorías declaradas (no oficiales)
# ---------------------------------------------------------------------------
def declaraciones_confirmadas(usuario):
    """
    Inscripciones CONFIRMADAS por el organizador donde figura esta cuenta con
    una categoría declarada (por sí misma como Jugador 1, o por un tercero
    como compañero). Son las únicas declaraciones que cuentan como "válidas":
    una inscripción pendiente o rechazada no fija ningún piso.
    """
    confirmadas = Inscripcion.objects.filter(estado=Inscripcion.ESTADO_CONFIRMADA)
    como_j1 = confirmadas.filter(usuario=usuario, categoria_oficial_1__isnull=False)
    como_j2 = confirmadas.filter(usuario_2=usuario, categoria_oficial_2__isnull=False)
    return como_j1, como_j2


def categoria_minima_declarada(usuario):
    """
    Mejor categoría (número más chico) ya declarada para esta cuenta en
    inscripciones confirmadas, o None. Es el PISO: quien no tiene categoría
    oficial no puede confirmar ni declarar una inferior a esta.
    """
    como_j1, como_j2 = declaraciones_confirmadas(usuario)
    valores = [
        v
        for v in (
            como_j1.aggregate(m=Min("categoria_oficial_1"))["m"],
            como_j2.aggregate(m=Min("categoria_oficial_2"))["m"],
        )
        if v is not None
    ]
    return min(valores) if valores else None


def declaradas_por_terceros(usuario):
    """Inscripciones donde OTRA persona declaró la categoría de esta cuenta
    (para mostrarlas al titular y que las confirme)."""
    return list(
        Inscripcion.objects.filter(
            usuario_2=usuario, estado=Inscripcion.ESTADO_CONFIRMADA, categoria_oficial_2__isnull=False
        )
        .select_related("torneo")
        .order_by("torneo__fecha_inicio")
    )


# ---------------------------------------------------------------------------
# Disponibilidad estructurada
# ---------------------------------------------------------------------------
_FRANJA = re.compile(r"^\s*(\S+)\s+de\s+(\d{1,2}):(\d{2})\s+a\s+(\d{1,2}):(\d{2})\s*$")


def registrar_disponibilidad_desde_texto(inscripcion):
    """
    Convierte el texto que compila el widget del formulario
    ("Lunes de 16:00 a 20:00, Viernes de 18:00 a 22:00") en filas de
    `DisponibilidadInscripcion`. Se usa SOLO para inscripciones nuevas, cuyo
    formato controlamos nosotros. Lo histórico NO se migra: no se puede
    garantizar que el texto viejo tenga este formato.

    Es idempotente (si ya hay filas no hace nada) y tolerante: las franjas
    que no se pueden interpretar se ignoran en vez de fallar.
    """
    if not inscripcion.disponibilidad or inscripcion.disponibilidades.exists():
        return 0
    creadas = []
    for parte in inscripcion.disponibilidad.split(","):
        m = _FRANJA.match(parte)
        if not m:
            continue
        dia = DisponibilidadInscripcion.DIA_POR_NOMBRE.get(m.group(1).lower())
        h1, m1, h2, m2 = (int(g) for g in m.groups()[1:])
        if dia is None or not (h1 < 24 and h2 < 24 and m1 < 60 and m2 < 60):
            continue
        desde, hasta = (h1, m1), (h2, m2)
        if hasta <= desde:
            continue
        creadas.append(
            DisponibilidadInscripcion(
                inscripcion=inscripcion,
                dia=dia,
                hora_desde=f"{h1:02d}:{m1:02d}",
                hora_hasta=f"{h2:02d}:{m2:02d}",
            )
        )
    DisponibilidadInscripcion.objects.bulk_create(creadas)
    return len(creadas)
