import json

from django.core.management.base import BaseCommand
from django.db import connection

from torneos.preflight import ejecutar_preflight

ESCRITURAS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "TRUNCATE")


class EscrituraProhibida(RuntimeError):
    pass


def _solo_lectura(execute, sql, params, many, context):
    """Cinturón y tirantes: si algo intentara escribir, el preflight se corta."""
    if sql.lstrip().upper().startswith(ESCRITURAS):
        raise EscrituraProhibida(f"El preflight es de solo lectura; se intentó: {sql[:60]}")
    return execute(sql, params, many, context)


class Command(BaseCommand):
    help = (
        "Informe de SOLO LECTURA sobre identidades, DNI e inscripciones, para revisar antes de "
        "aplicar las migraciones de identidad y rankings. No modifica nada."
    )

    def add_arguments(self, parser):
        parser.add_argument("--completo", action="store_true",
                            help="Muestra DNI, nombres y usuarios completos. Por defecto se enmascaran "
                                 "para poder compartir el informe sin exponer datos personales.")
        parser.add_argument("--json", action="store_true", help="Salida en JSON.")
        parser.add_argument("--detalle", type=int, default=5,
                            help="Cuántos casos mostrar por lista (default 5). Usá 0 para todos.")

    def handle(self, *args, **opts):
        with connection.execute_wrapper(_solo_lectura):
            datos = ejecutar_preflight(anonimizar=not opts["completo"])

        if opts["json"]:
            self.stdout.write(json.dumps(datos, ensure_ascii=False, indent=2, default=str))
            return
        self.stdout.write(self._texto(datos, opts["detalle"]))

    # ------------------------------------------------------------------
    def _texto(self, d, limite):
        L = []
        w = L.append

        def lista(titulo, items, formato):
            if not items:
                return
            w(f"\n  {titulo} ({len(items)}):")
            for item in (items if limite == 0 else items[:limite]):
                w(f"    - {formato(item)}")
            if limite and len(items) > limite:
                w(f"    … y {len(items) - limite} más (usá --detalle 0 para verlos todos)")

        w("=" * 72)
        w("PREFLIGHT DE IDENTIDAD — solo lectura (no se modificó nada)")
        w("Datos personales: " + ("ENMASCARADOS (informe compartible)" if d["anonimizado"]
                                  else "COMPLETOS (no compartir)"))
        w("=" * 72)

        w("\n0. Migraciones")
        if d["migraciones_pendientes"]:
            w("   PENDIENTES (no son las de la entrega actual): " + ", ".join(d["migraciones_pendientes"]))
            w("   -> Aplicá primero las migraciones ya entregadas; este informe puede estar incompleto.")
        else:
            w("   OK: no hay migraciones previas sin aplicar.")
        if d.get("migraciones_a_aplicar"):
            w("   Por aplicar (entrega de identidad y rankings; este informe se corre ANTES de aplicarlas):")
            for m in d["migraciones_a_aplicar"]:
                w(f"     - {m}")

        w("\n1. Resumen")
        for k, v in d["resumen"].items():
            w(f"   {k.replace('_', ' '):<26} {v}")
        e = d["entidades"]
        w(f"   organizadores: {e['organizadores']} (con torneos: {e['con_torneos']}, con canchas: {e['con_canchas']})"
          f" -> se crearía {e['membresias_a_crear']} membresía(s) de administrador")
        c = d["categorias"]
        w(f"   jugadores sin categoría oficial: {c['jugadores_sin_categoria_oficial']} | inscripciones sin "
          f"categoría declarada (J1/J2): {c['inscripciones_sin_categoria_jugador_1']}/"
          f"{c['inscripciones_sin_categoria_jugador_2']}")

        w("\n2. Hallazgos")
        if not d["hallazgos"]:
            w("   Ninguno.")
        for h in d["hallazgos"]:
            etiqueta = "DECISIÓN HUMANA" if h["severidad"] == "decision_humana" else "informativo"
            w(f"   [{etiqueta}] {h['titulo']}: {h['cantidad']}")
            w(f"       -> {h['que_pasara']}")

        w("\n3. Detalle")
        ident, insc = d["identidades"], d["inscripciones"]
        lista("Cuentas con el mismo DNI normalizado", ident["grupos_duplicados"],
              lambda g: f"DNI {g['dni_normalizado']}: " + "; ".join(
                  f"cuenta {c['usuario_id']} ({c['usuario']}, DNI tipeado {c['dni_tal_cual']}, "
                  f"cat. oficial {c['categoria_oficial']})" for c in g["cuentas"]))
        lista("DNI sospechosos", ident["dni_sospechosos"],
              lambda i: f"cuenta {i['usuario_id']}, DNI {i['dni_tal_cual']} ({i['motivo']})")
        detalle = insc["detalle"]
        lista("DNI de inscripciones que coincide con varias cuentas", detalle.get("varias_identidades", []),
              lambda i: f"inscripción {i['inscripcion_id']} lado {i['lado']}, DNI {i['dni']}, cuentas {i['cuentas']}")
        lista("DNI de una cuenta pero nombre dudoso", detalle.get("una_identidad_nombre_dudoso", []),
              lambda i: f"inscripción {i['inscripcion_id']} lado {i['lado']}: declarado «{i['nombre_declarado']}» "
                        f"vs cuenta {i['cuenta_id']} «{i['nombre_cuenta']}»")
        lista("Mismo DNI sin cuenta con nombres incompatibles", insc["mismo_dni_nombres_distintos"],
              lambda i: f"DNI {i['dni']}: {', '.join(i['nombres'])} (inscripciones {i['inscripciones']})")
        lista("Vínculos retroactivos ya hechos solo por DNI", insc["vinculos_retroactivos_sin_verificar"],
              lambda i: f"inscripción {i['inscripcion_id']} ({i['torneo']}, {i['inscripta']}) -> cuenta "
                        f"{i['cuenta_id']} creada {i['cuenta_creada']}")
        lista("DNI tipeado distinto del de la cuenta (jugador 1)", insc["jugador_1_dni_distinto_de_su_cuenta"],
              lambda i: f"inscripción {i['inscripcion_id']}: tipeó {i['dni_tipeado']}, cuenta {i['dni_de_la_cuenta']}")
        lista("DNI tipeado distinto del de la cuenta (compañero)", insc["companero_dni_distinto_de_su_cuenta"],
              lambda i: f"inscripción {i['inscripcion_id']}: tipeó {i['dni_tipeado']}, cuenta {i['dni_de_la_cuenta']}")
        w("\n   Clasificación de jugadores en inscripciones (ambos lados):")
        for k, v in insc["por_clasificacion"].items():
            w(f"     {k.replace('_', ' '):<38} {v}")
        w(f"     {'vínculos hechos al inscribir (ok)':<38} {insc['vinculos_hechos_al_inscribir']}")
        w(f"\n   Casos de revisión ya existentes: {d['revisiones']['abiertas']} abiertos, "
          f"{d['revisiones']['resueltas']} resueltos")

        w("\n4. Qué haría la migración")
        for k, v in d["proyeccion"].items():
            w(f"   {k.replace('_', ' '):<48} {v}")

        w("\n5. Veredicto")
        decisiones = [h for h in d["hallazgos"] if h["severidad"] == "decision_humana"]
        if d["migraciones_pendientes"]:
            w("   NO MIGRAR TODAVÍA: faltan migraciones previas.")
        else:
            w("   Las migraciones son solo aditivas: no borran ni modifican datos existentes.")
            if decisiones:
                w(f"   {len(decisiones)} tipo(s) de caso necesitan decisión humana. La migración los deja")
                w("   MARCADOS; nada se vincula, se crea ni se fusiona sin tu aprobación.")
            else:
                w("   No hay casos que requieran decisión humana.")
        w("=" * 72)
        return "\n".join(L)
