"""
Preflight de identidad (SOLO LECTURA).

Examina los datos existentes ANTES de aplicar las migraciones de identidad y
rankings, y responde: ¿qué va a encontrar la migración? ¿qué casos van a
necesitar una decisión humana? No escribe nada: toda la lógica son consultas.

Principio: coincidencia de DNI o de nombre = INDICIO, nunca verificación. Este
módulo solo clasifica; no vincula, no crea ni fusiona identidades.

`anonimizar=True` (por defecto en el comando) enmascara DNI, nombres y
usuarios para que el informe se pueda compartir sin exponer datos personales.
"""
from collections import defaultdict

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Identidad, Jugador, Organizador, nombres_compatibles, normalizar_dni

from .models import Categoria, Inscripcion, RevisionIdentidad, Torneo

APPS_CON_MIGRACIONES_PREVIAS = ("accounts", "torneos", "competencia", "rankings")
# Las migraciones de la entrega de identidad y rankings: es NORMAL que estén sin aplicar cuando se
# corre el preflight (justamente se corre antes de aplicarlas). Cualquier otra pendiente es un problema.
MIGRACIONES_DE_ESTA_ENTREGA = {
    "accounts.0008_identidad_persona_reclamos_membresias",
    "accounts.0009_backfill_identidad_y_membresias",
    "accounts.0010_identidad_dni_unico",
    "torneos.0012_personas_en_inscripciones",
    "torneos.0013_backfill_personas_seguras",
    "competencia.0002_propuestaasignacion_puntaje_pareja_and_more",
    "competencia.0003_propuestafase_ranking_version_and_more",
    "rankings.0001_initial",
    "torneos.0014_inscripcion_apellidos_y_multicategoria",
    "torneos.0015_inscripcion_estado_cancelada",
}
# Columnas que solo existen DESPUÉS de migrar: el preflight no las lee para poder correr antes.
CAMPOS_NUEVOS_IDENTIDAD = ("dni_normalizado", "dni_en_revision", "nombre", "origen")
CAMPOS_NUEVOS_INSCRIPCION = ("persona_1", "persona_2", "apellido_1", "apellido_2", "torneo__permite_multiples_categorias")
LARGO_DNI_RAZONABLE = range(6, 10)  # 6 a 9 dígitos


# ---------------------------------------------------------------------------
# Enmascarado (para poder compartir el informe)
# ---------------------------------------------------------------------------
def _dni(valor, anonimizar):
    if not anonimizar:
        return valor
    digitos = normalizar_dni(valor)
    return f"***{digitos[-3:]}" if len(digitos) > 3 else "***"


def _nombre(valor, anonimizar):
    if not anonimizar:
        return valor
    iniciales = [p[0].upper() + "." for p in (valor or "").split() if p]
    return " ".join(iniciales) or "(sin nombre)"


def _usuario(valor, anonimizar):
    if not anonimizar:
        return valor
    return (valor[:2] + "***") if valor else ""


# ---------------------------------------------------------------------------
def migraciones_pendientes():
    """Migraciones sin aplicar de las apps que el preflight necesita (solo lectura)."""
    ejecutor = MigrationExecutor(connection)
    plan = ejecutor.migration_plan(ejecutor.loader.graph.leaf_nodes())
    return [f"{m.app_label}.{m.name}" for m, _ in plan if m.app_label in APPS_CON_MIGRACIONES_PREVIAS]


def _nombre_de(identidad):
    if identidad.usuario_id is None:  # solo existe después de migrar (antes toda identidad tiene cuenta)
        return identidad.nombre
    usuario = identidad.usuario
    nombre = usuario.get_full_name().strip()
    if nombre:
        return nombre
    jugador = getattr(usuario, "jugador", None)
    return (jugador.nombre if jugador is not None else "") or ""


def _ficha_identidad(identidad, anonimizar):
    usuario = identidad.usuario
    return {
        "usuario_id": identidad.usuario_id,
        "usuario": _usuario(usuario.username if usuario else "(sin cuenta)", anonimizar),
        "dni_tal_cual": _dni(identidad.dni, anonimizar),
        "nombre": _nombre(_nombre_de(identidad), anonimizar),
        "creada": identidad.creado.date().isoformat(),
        "tiene_jugador": usuario is not None and hasattr(usuario, "jugador"),
        "tiene_organizador": usuario is not None and hasattr(usuario, "organizador"),
        "categoria_oficial": getattr(getattr(usuario, "jugador", None), "categoria_oficial", None),
    }


def _hallazgo(severidad, codigo, titulo, cantidad, que_pasara):
    return {
        "severidad": severidad,  # decision_humana | informativo
        "codigo": codigo,
        "titulo": titulo,
        "cantidad": cantidad,
        "que_pasara": que_pasara,
    }


def ejecutar_preflight(anonimizar=True):
    """Devuelve un diccionario con todos los hallazgos. No escribe en la base."""
    identidades = list(
        Identidad.objects.select_related("usuario", "usuario__jugador", "usuario__organizador")
        .defer(*CAMPOS_NUEVOS_IDENTIDAD)
        .order_by("pk")
    )
    por_dni = defaultdict(list)
    for identidad in identidades:
        por_dni[normalizar_dni(identidad.dni)].append(identidad)

    # --- 1. Identidades: duplicados y DNI raros -----------------------------
    grupos_duplicados = [
        {
            "dni_normalizado": _dni(dni, anonimizar),
            "cantidad": len(grupo),
            "cuentas": [_ficha_identidad(i, anonimizar) for i in grupo],
        }
        for dni, grupo in sorted(por_dni.items())
        if dni and len(grupo) > 1
    ]
    dni_sospechosos = [
        {**_ficha_identidad(i, anonimizar), "motivo": "sin dígitos" if not normalizar_dni(i.dni) else "largo inusual"}
        for i in identidades
        if len(normalizar_dni(i.dni)) not in LARGO_DNI_RAZONABLE
    ]
    dni_no_canonicos = sum(1 for i in identidades if i.dni != normalizar_dni(i.dni))

    # --- 2. Inscripciones: cómo se identificaría a cada jugador -------------
    lados = defaultdict(list)  # clasificación -> detalles
    dni_a_inscripciones = defaultdict(list)  # dni normalizado SIN identidad -> [(inscripcion, nombre)]
    dnis_distintos, dnis_con_identidad, dnis_nuevos = set(), set(), set()
    inscripciones = list(
        Inscripcion.objects.select_related("torneo", "usuario", "usuario_2").defer(*CAMPOS_NUEVOS_INSCRIPCION).order_by("pk")
    )

    for insc in inscripciones:
        for n in (1, 2):
            dni = getattr(insc, f"dni_{n}")
            nombre = getattr(insc, f"nombre_{n}")
            normalizado = normalizar_dni(dni)
            base = {
                "inscripcion_id": insc.pk,
                "torneo": insc.torneo.nombre,
                "lado": n,
                "dni": _dni(dni, anonimizar),
                "nombre_declarado": _nombre(nombre, anonimizar),
            }
            if len(normalizado) not in LARGO_DNI_RAZONABLE:
                # Vacío o de largo imposible ("1", "123", "s/d"): no hay forma segura de
                # identificar a la persona, así que no se proyecta crear ninguna.
                lados["sin_dni_valido"].append(base)
                continue
            dnis_distintos.add(normalizado)
            candidatas = por_dni.get(normalizado, [])
            if not candidatas:
                lados["sin_identidad"].append(base)
                dni_a_inscripciones[normalizado].append((insc, nombre))
                dnis_nuevos.add(normalizado)
            elif len(candidatas) > 1:
                lados["varias_identidades"].append({**base, "cuentas": [c.usuario_id for c in candidatas]})
                dnis_con_identidad.add(normalizado)
            else:
                identidad = candidatas[0]
                dnis_con_identidad.add(normalizado)
                compatible = nombres_compatibles(nombre, _nombre_de(identidad))
                clave = {True: "una_identidad_nombre_compatible", False: "una_identidad_nombre_dudoso",
                         None: "una_identidad_nombre_sin_datos"}[compatible]
                lados[clave].append({**base, "cuenta_id": identidad.usuario_id,
                                     "nombre_cuenta": _nombre(_nombre_de(identidad), anonimizar)})

    # Mismo DNI (sin identidad) con nombres incompatibles entre inscripciones: ¿error de tipeo o dos personas?
    mismo_dni_nombres_distintos = []
    for dni, items in sorted(dni_a_inscripciones.items()):
        nombres = sorted({n for _, n in items})
        incompatibles = any(
            nombres_compatibles(a, b) is False for i, a in enumerate(nombres) for b in nombres[i + 1:]
        )
        if incompatibles:
            mismo_dni_nombres_distintos.append({
                "dni": _dni(dni, anonimizar),
                "inscripciones": sorted({i.pk for i, _ in items}),
                "nombres": [_nombre(n, anonimizar) for n in nombres],
            })

    # --- 3. Coherencia entre las cuentas enlazadas y el DNI tipeado ---------
    con_cuenta = [i for i in identidades if i.usuario_id is not None]  # las personas sin cuenta no cuentan acá
    cuenta_dni = {i.usuario_id: normalizar_dni(i.dni) for i in con_cuenta}
    cuenta_creada = {i.usuario_id: i.creado for i in con_cuenta}
    jugador1_dni_distinto, companero_dni_distinto = [], []
    vinculos_retroactivos, vinculos_al_inscribir, misma_persona_ambos_lados = [], 0, []
    for insc in inscripciones:
        d1, d2 = normalizar_dni(insc.dni_1), normalizar_dni(insc.dni_2)
        if d1 and d1 == d2:
            misma_persona_ambos_lados.append(insc.pk)
        if insc.usuario_id and insc.usuario_id in cuenta_dni and d1 != cuenta_dni[insc.usuario_id]:
            jugador1_dni_distinto.append({
                "inscripcion_id": insc.pk, "dni_tipeado": _dni(insc.dni_1, anonimizar),
                "dni_de_la_cuenta": _dni(cuenta_dni[insc.usuario_id], anonimizar),
            })
        if insc.usuario_2_id and insc.usuario_2_id in cuenta_dni:
            if d2 != cuenta_dni[insc.usuario_2_id]:
                companero_dni_distinto.append({
                    "inscripcion_id": insc.pk, "dni_tipeado": _dni(insc.dni_2, anonimizar),
                    "dni_de_la_cuenta": _dni(cuenta_dni[insc.usuario_2_id], anonimizar),
                })
            elif insc.creado < cuenta_creada[insc.usuario_2_id]:
                # La inscripción es ANTERIOR a la cuenta: se vinculó por DNI después, sin verificar.
                vinculos_retroactivos.append({
                    "inscripcion_id": insc.pk, "torneo": insc.torneo.nombre,
                    "cuenta_id": insc.usuario_2_id, "inscripta": insc.creado.date().isoformat(),
                    "cuenta_creada": cuenta_creada[insc.usuario_2_id].date().isoformat(),
                })
            else:
                vinculos_al_inscribir += 1

    # --- 4. Casos de revisión ya existentes ---------------------------------
    revisiones = {
        "abiertas": RevisionIdentidad.objects.filter(estado=RevisionIdentidad.ESTADO_ABIERTA).count(),
        "resueltas": RevisionIdentidad.objects.filter(estado=RevisionIdentidad.ESTADO_RESUELTA).count(),
    }

    # --- 5. Resumen general, entidades y categorías -------------------------
    resumen = {
        "cuentas": Identidad._meta.get_field("usuario").related_model.objects.count(),
        "identidades": len(identidades),
        "perfiles_jugador": Jugador.objects.count(),
        "perfiles_organizador": Organizador.objects.count(),
        "torneos": Torneo.objects.count(),
        "categorias_de_torneo": Categoria.objects.count(),
        "inscripciones": len(inscripciones),
    }
    entidades = {
        "organizadores": Organizador.objects.count(),
        "con_torneos": Organizador.objects.filter(torneos__isnull=False).distinct().count(),
        "con_canchas": Organizador.objects.filter(canchas__isnull=False).distinct().count(),
        "membresias_a_crear": Organizador.objects.count(),  # una de administrador por cada una
    }
    categorias = {
        "jugadores_sin_categoria_oficial": Jugador.objects.filter(categoria_oficial__isnull=True).count(),
        "inscripciones_sin_categoria_jugador_1": Inscripcion.objects.filter(categoria_oficial_1__isnull=True).count(),
        "inscripciones_sin_categoria_jugador_2": Inscripcion.objects.filter(categoria_oficial_2__isnull=True).count(),
    }

    # --- 6. Qué haría la migración con todo esto ----------------------------
    con_cuenta_fk = sum(1 for i in inscripciones if i.usuario_id and i.usuario_id in cuenta_dni)
    con_cuenta_fk += sum(1 for i in inscripciones if i.usuario_2_id and i.usuario_2_id in cuenta_dni)
    proyeccion = {
        "dnis_distintos_en_inscripciones": len(dnis_distintos),
        "dnis_que_ya_tienen_identidad": len(dnis_con_identidad),
        "personas_nuevas_sin_cuenta_que_se_crearian": len(dnis_nuevos),
        "vinculos_seguros_por_cuenta_enlazada": con_cuenta_fk,
        "identidades_a_marcar_en_revision": sum(g["cantidad"] for g in grupos_duplicados),
    }

    pendientes = migraciones_pendientes()
    hallazgos = []

    def agregar(severidad, codigo, titulo, cantidad, que_pasara):
        if cantidad:
            hallazgos.append(_hallazgo(severidad, codigo, titulo, cantidad, que_pasara))

    agregar("decision_humana", "identidades_duplicadas", "Cuentas con el mismo DNI (comparado sin puntos)",
            len(grupos_duplicados),
            "Quedan marcadas en revisión. No se fusionan ni se borran; las resuelve una persona.")
    agregar("decision_humana", "inscripciones_dni_ambiguo", "Jugadores de inscripciones cuyo DNI coincide con varias cuentas",
            len(lados["varias_identidades"]), "No se vinculan; queda un caso de revisión abierto.")
    agregar("decision_humana", "coincidencia_nombre_dudosa",
            "Jugadores cuyo DNI coincide con UNA cuenta pero el nombre no se parece",
            len(lados["una_identidad_nombre_dudoso"]),
            "Posible error de tipeo del DNI. No se vinculan; quedan en revisión.")
    agregar("decision_humana", "mismo_dni_nombres_distintos", "Un mismo DNI sin cuenta con nombres incompatibles",
            len(mismo_dni_nombres_distintos), "No se crea una persona: se revisa si es un error o son dos personas.")
    agregar("decision_humana", "vinculos_retroactivos_sin_verificar",
            "Inscripciones anteriores a la cuenta que ya quedaron vinculadas solo por DNI",
            len(vinculos_retroactivos), "No se deshacen. Quedan listadas para que decidas si se verifican.")
    agregar("decision_humana", "cuenta_vs_dni_inconsistente",
            "Inscripciones donde el DNI tipeado no coincide con el de la cuenta enlazada",
            len(jugador1_dni_distinto) + len(companero_dni_distinto), "No se vinculan por DNI; se revisan.")
    agregar("informativo", "dni_sin_dni_valido", "Jugadores sin DNI utilizable en inscripciones (vacío o de largo imposible)",
            len(lados["sin_dni_valido"]), "Quedan sin persona asociada (no hay forma segura de identificarlos).")
    agregar("informativo", "dni_sospechosos", "Identidades con DNI de largo inusual o sin dígitos",
            len(dni_sospechosos), "Se normalizan igual; conviene revisarlos.")
    agregar("informativo", "dni_no_canonicos", "DNI guardados con puntos o espacios", dni_no_canonicos,
            "Se agrega el DNI normalizado al lado; el original no se modifica.")
    agregar("informativo", "misma_persona_ambos_lados", "Inscripciones con el mismo DNI en los dos jugadores",
            len(misma_persona_ambos_lados), "Dato incoherente: no se asocian personas hasta corregirlo.")
    agregar("informativo", "personas_a_crear", "DNI de inscripciones sin identidad (personas sin cuenta a crear)",
            len(dnis_nuevos), "Se crean recién con tu aprobación, mediante un comando aparte en modo simulación.")

    return {
        "migraciones_pendientes": [m for m in pendientes if m not in MIGRACIONES_DE_ESTA_ENTREGA],
        "migraciones_a_aplicar": [m for m in pendientes if m in MIGRACIONES_DE_ESTA_ENTREGA],
        "resumen": resumen,
        "entidades": entidades,
        "categorias": categorias,
        "identidades": {
            "grupos_duplicados": grupos_duplicados,
            "dni_sospechosos": dni_sospechosos,
            "dni_no_canonicos": dni_no_canonicos,
        },
        "inscripciones": {
            "por_clasificacion": {k: len(v) for k, v in sorted(lados.items())},
            "detalle": {k: v for k, v in sorted(lados.items()) if not k.endswith("_compatible")},
            "mismo_dni_nombres_distintos": mismo_dni_nombres_distintos,
            "jugador_1_dni_distinto_de_su_cuenta": jugador1_dni_distinto,
            "companero_dni_distinto_de_su_cuenta": companero_dni_distinto,
            "misma_persona_en_ambos_lados": misma_persona_ambos_lados,
            "vinculos_retroactivos_sin_verificar": vinculos_retroactivos,
            "vinculos_hechos_al_inscribir": vinculos_al_inscribir,
        },
        "revisiones": revisiones,
        "proyeccion": proyeccion,
        "hallazgos": hallazgos,
        "anonimizado": anonimizar,
    }
