from collections import Counter

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from accounts.identidad import candidatas_por_dni, crear_persona_sin_cuenta, nombre_para_comparar
from accounts.models import Identidad, nombres_compatibles, normalizar_dni
from torneos.management.commands.preflight_identidad import EscrituraProhibida, _solo_lectura  # noqa: F401
from torneos.models import Inscripcion, RevisionIdentidad
from torneos.servicios import LARGO_DNI_RAZONABLE, registrar_revision_dni

ETIQUETAS = {
    "ya_asignada": "ya tenían persona (no se toca)",
    "sin_dni_valido": "sin DNI utilizable (queda sin persona)",
    "crear_persona": "se crearía una persona sin cuenta",
    "vincular_a_nueva": "se vincularía a una persona que este mismo proceso crea",
    "vincular": "se vincularía a una identidad existente",
    "retroactivo": "DNI de una cuenta creada DESPUÉS de la inscripción -> revisión (no se vincula)",
    "nombre_dudoso": "DNI coincide pero el nombre no -> revisión (no se vincula)",
    "ambiguo": "DNI de varias identidades / en revisión -> revisión (no se vincula)",
    "misma_persona": "ya está en el otro lado de la inscripción (no se asocia)",
    "cuenta_sin_identidad": "cuenta sin identidad (reclamo pendiente): se completa al resolverlo",
}


ETIQUETAS_APLICADO = {
    **ETIQUETAS,
    "crear_persona": "personas sin cuenta CREADAS",
    "vincular_a_nueva": "vinculadas a una persona creada en esta corrida",
    "vincular": "vinculadas a una identidad existente",
    "retroactivo": "DNI de una cuenta creada DESPUÉS de la inscripción -> caso de revisión abierto (no se vinculó)",
    "nombre_dudoso": "DNI coincide pero el nombre no -> caso de revisión abierto (no se vinculó)",
    "ambiguo": "DNI de varias identidades / en revisión -> caso de revisión abierto (no se vinculó)",
}


def planificar(insc, lado, previstas):
    """Qué se haría con un lado de una inscripción. `previstas` simula las personas que la corrida crearía."""
    if getattr(insc, f"persona_{lado}_id"):
        return "ya_asignada", None
    if lado == 1 and insc.usuario_id:
        # El jugador 1 con cuenta sale de su cuenta, nunca del DNI tipeado.
        propia = Identidad.objects.filter(usuario_id=insc.usuario_id).first()
        return ("vincular", propia) if propia else ("cuenta_sin_identidad", None)
    dni, nombre = getattr(insc, f"dni_{lado}"), getattr(insc, f"nombre_completo_{lado}")
    normalizado = normalizar_dni(dni)
    if len(normalizado) not in LARGO_DNI_RAZONABLE:
        return "sin_dni_valido", None
    candidatas = candidatas_por_dni(dni)
    if not candidatas:
        previo = previstas.get(normalizado)
        if previo is None:
            previstas[normalizado] = nombre
            return "crear_persona", None
        return ("nombre_dudoso", None) if nombres_compatibles(nombre, previo) is False else ("vincular_a_nueva", None)
    if len(candidatas) > 1 or candidatas[0].dni_en_revision:
        return "ambiguo", candidatas
    identidad = candidatas[0]
    if identidad.usuario_id and identidad.creado > insc.creado:
        return "retroactivo", identidad
    if nombres_compatibles(nombre, nombre_para_comparar(identidad)) is False:
        return "nombre_dudoso", identidad
    return "vincular", identidad


class Command(BaseCommand):
    help = (
        "Asocia las inscripciones históricas con su identidad (persona) usando el DNI tipeado. Por "
        "DEFECTO SOLO SIMULA y muestra qué haría. Con --aplicar escribe. Nunca fusiona ni borra "
        "identidades, nunca toca datos declarados, y deja los casos dudosos en revisión."
    )

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Escribe los cambios (si no, solo simula).")
        parser.add_argument("--detalle", type=int, default=5, help="Ejemplos a mostrar por tipo (default 5).")

    def handle(self, *args, **opts):
        aplicar = opts["aplicar"]
        previstas, conteo, ejemplos = {}, Counter(), {}
        inscripciones = list(Inscripcion.objects.order_by("pk"))

        def ejecutar():
            for insc in inscripciones:
                insc.refresh_from_db()
                for lado in (1, 2):
                    accion, destino = planificar(insc, lado, previstas)
                    if accion in ("vincular", "vincular_a_nueva", "crear_persona") and aplicar:
                        accion = self._aplicar(insc, lado, accion, destino)
                    elif accion in ("ambiguo", "nombre_dudoso", "retroactivo") and aplicar:
                        self._abrir_revision(insc, lado, accion, destino)
                    conteo[(lado, accion)] += 1
                    ejemplos.setdefault(accion, []).append(f"inscripción {insc.pk} (jugador {lado})")

        if aplicar:
            with transaction.atomic():
                ejecutar()
        else:
            with connection.execute_wrapper(_solo_lectura):
                ejecutar()

        w = self.stdout.write
        w("=" * 70)
        w(("APLICADO" if aplicar else "SIMULACIÓN (no se escribió nada; usá --aplicar para ejecutar)"))
        w("=" * 70)
        etiquetas = ETIQUETAS_APLICADO if aplicar else ETIQUETAS
        for accion, etiqueta in etiquetas.items():
            total = sum(v for (lado, a), v in conteo.items() if a == accion)
            if not total:
                continue
            w(f"{etiqueta}: {total}")
            for ejemplo in ejemplos[accion][: opts["detalle"]]:
                w(f"    - {ejemplo}")
        w(f"Casos de revisión abiertos ahora: {RevisionIdentidad.objects.filter(estado='abierta').count()}")

    # ------------------------------------------------------------------
    def _aplicar(self, insc, lado, accion, identidad):
        """Escribe la asociación. Devuelve la acción que realmente ocurrió (para el informe)."""
        if accion == "crear_persona":
            identidad = crear_persona_sin_cuenta(
                getattr(insc, f"dni_{lado}"), getattr(insc, f"nombre_completo_{lado}"), getattr(insc, f"localidad_{lado}", ""),
                origen=Identidad.ORIGEN_MIGRACION,
            )
        elif accion == "vincular_a_nueva":
            candidatas = candidatas_por_dni(getattr(insc, f"dni_{lado}"))
            identidad = candidatas[0] if len(candidatas) == 1 else None
        if identidad is None:
            return "sin_dni_valido"  # no se pudo identificar con certeza: queda sin persona
        otro = insc.persona_2_id if lado == 1 else insc.persona_1_id
        if otro == identidad.pk:
            return "misma_persona"  # la misma persona en ambos lados: dato incoherente
        setattr(insc, f"persona_{lado}", identidad)
        insc.save(update_fields=[f"persona_{lado}"])
        return accion

    def _abrir_revision(self, insc, lado, accion, destino):
        dni = getattr(insc, f"dni_{lado}")
        if accion == "ambiguo":
            registrar_revision_dni(dni, [c.usuario for c in destino if c.usuario_id], [insc])
        else:
            motivo = (RevisionIdentidad.MOTIVO_NOMBRE_NO_COINCIDE if accion == "nombre_dudoso"
                      else RevisionIdentidad.MOTIVO_VINCULO_RETROACTIVO)
            registrar_revision_dni(dni, [destino.usuario] if destino is not None and destino.usuario_id else [], [insc], motivo)
