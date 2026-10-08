import datetime
import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Identidad, Jugador, Organizador, nombres_compatibles

from .management.commands.preflight_identidad import EscrituraProhibida, _solo_lectura
from .models import Categoria, Inscripcion, RevisionIdentidad, Torneo
from .preflight import ejecutar_preflight, migraciones_pendientes
from .servicios import registrar_revision_dni
from .testing import crear_categoria, crear_inscripcion, crear_usuario

User = get_user_model()
ESCRITURAS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER")


def _con_nombre(dni, nombre, apellido, categoria=None):
    usuario = crear_usuario(dni=dni, categoria_oficial=categoria)
    usuario.first_name, usuario.last_name = nombre, apellido
    usuario.save()
    return usuario


class NombresCompatiblesTests(TestCase):
    def test_ignora_orden_acentos_y_mayusculas(self):
        self.assertTrue(nombres_compatibles("Juan Pérez", "PEREZ, juan"))
        self.assertTrue(nombres_compatibles("María José Gómez", "Gomez Maria"))

    def test_basta_una_palabra_significativa_en_comun(self):
        self.assertTrue(nombres_compatibles("Juan Pérez", "Juan Carlos Ruiz"))

    def test_nombres_distintos(self):
        self.assertFalse(nombres_compatibles("Carlos Ruiz", "Mariano López"))

    def test_las_particulas_y_siglas_cortas_no_cuentan(self):
        self.assertFalse(nombres_compatibles("Ana de la Vega", "Luis de la Torre"))  # solo 'de', 'la'
        self.assertIsNone(nombres_compatibles("J. P.", "Juan Pérez"))  # sin datos suficientes

    def test_sin_datos_no_se_puede_comparar(self):
        self.assertIsNone(nombres_compatibles("", "Juan Pérez"))
        self.assertIsNone(nombres_compatibles(None, None))


class PreflightHallazgosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana1 = _con_nombre("27.555.000", "Ana", "Gómez", 6)          # DNI con puntos...
        cls.ana2 = _con_nombre("27555000", "Ana", "Gomez Duplicada", 8)  # ...y el mismo sin puntos
        cls.carlos = _con_nombre("30100001", "Carlos", "Ruiz", 5)
        cls.raro = _con_nombre("123", "Pedro", "Sosa")
        cls.cat = crear_categoria("6ta LIBRE")
        c = cls.cat
        cls.compatible = crear_inscripcion(c, dni_1="55000001", dni_2="30100001", nombre_2="Carlos Ruiz")
        cls.dudoso = crear_inscripcion(c, dni_1="55000002", dni_2="30100001", nombre_2="Mariano López")
        cls.ambiguo = crear_inscripcion(c, dni_1="55000003", dni_2="27555000", nombre_2="Ana Gómez")
        cls.nuevo_a = crear_inscripcion(c, dni_1="55000004", dni_2="40111222", nombre_2="Laura Paz")
        cls.nuevo_b = crear_inscripcion(c, dni_1="55000005", dni_2="40.111.222", nombre_2="Roberto Díaz")
        cls.sin_dni = crear_inscripcion(c, dni_1="55000006", dni_2="s/d", nombre_2="Sin Dato")
        cls.fk_distinto = crear_inscripcion(c, usuario=cls.carlos, dni_1="99999999", dni_2="55000007")
        cls.misma = crear_inscripcion(c, dni_1="55000008", dni_2="55.000.008")
        cls.retro = crear_inscripcion(c, dni_1="55000009", dni_2="30100001", usuario_2=cls.carlos)
        Inscripcion.objects.filter(pk=cls.retro.pk).update(
            creado=cls.carlos.identidad.creado - datetime.timedelta(days=30)
        )
        cls.al_inscribir = crear_inscripcion(c, dni_1="55000010", dni_2="30100001", usuario_2=cls.carlos)
        registrar_revision_dni("27555000", [cls.ana1, cls.ana2], [cls.ambiguo])
        cls.d = ejecutar_preflight(anonimizar=False)

    def _ids(self, clave):
        return {i["inscripcion_id"] for i in self.d["inscripciones"]["detalle"].get(clave, [])}

    def test_detecta_cuentas_con_el_mismo_dni_aunque_cambie_el_formato(self):
        grupos = self.d["identidades"]["grupos_duplicados"]
        self.assertEqual(len(grupos), 1)
        self.assertEqual({c["usuario_id"] for c in grupos[0]["cuentas"]}, {self.ana1.pk, self.ana2.pk})

    def test_dni_sospechosos_y_no_canonicos(self):
        self.assertEqual([i["usuario_id"] for i in self.d["identidades"]["dni_sospechosos"]], [self.raro.pk])
        self.assertEqual(self.d["identidades"]["dni_no_canonicos"], 1)  # "27.555.000"

    def test_clasifica_a_cada_jugador_de_las_inscripciones(self):
        self.assertIn(self.ambiguo.pk, self._ids("varias_identidades"))
        self.assertIn(self.dudoso.pk, self._ids("una_identidad_nombre_dudoso"))
        self.assertNotIn(self.compatible.pk, self._ids("una_identidad_nombre_dudoso"))
        self.assertIn(self.sin_dni.pk, self._ids("sin_dni_valido"))
        self.assertIn(self.nuevo_a.pk, self._ids("sin_identidad"))
        self.assertGreaterEqual(self.d["inscripciones"]["por_clasificacion"]["una_identidad_nombre_compatible"], 1)

    def test_un_dni_de_largo_imposible_es_inutilizable_y_no_genera_una_persona(self):
        junk = crear_inscripcion(self.cat, dni_1="55000011", dni_2="1", nombre_2="Cualquiera")
        d = ejecutar_preflight(anonimizar=False)
        ids = {i["inscripcion_id"] for i in d["inscripciones"]["detalle"]["sin_dni_valido"]}
        self.assertIn(junk.pk, ids)
        nuevos_sin_junk = self.d["proyeccion"]["personas_nuevas_sin_cuenta_que_se_crearian"]
        self.assertEqual(d["proyeccion"]["personas_nuevas_sin_cuenta_que_se_crearian"], nuevos_sin_junk + 1)  # solo 55000011

    def test_mismo_dni_sin_cuenta_con_nombres_incompatibles(self):
        casos = self.d["inscripciones"]["mismo_dni_nombres_distintos"]
        self.assertIn({self.nuevo_a.pk, self.nuevo_b.pk}, [set(c["inscripciones"]) for c in casos])
        # La inscripción con el mismo DNI en ambos lados también tiene nombres incompatibles: se informa.
        self.assertIn({self.misma.pk}, [set(c["inscripciones"]) for c in casos])
        self.assertEqual(len(casos), 2)

    def test_inconsistencias_entre_la_cuenta_enlazada_y_el_dni_tipeado(self):
        ids = {i["inscripcion_id"] for i in self.d["inscripciones"]["jugador_1_dni_distinto_de_su_cuenta"]}
        self.assertIn(self.fk_distinto.pk, ids)
        self.assertEqual(self.d["inscripciones"]["misma_persona_en_ambos_lados"], [self.misma.pk])

    def test_distingue_vinculo_retroactivo_de_vinculo_al_inscribir(self):
        retro = {i["inscripcion_id"] for i in self.d["inscripciones"]["vinculos_retroactivos_sin_verificar"]}
        self.assertEqual(retro, {self.retro.pk})
        self.assertGreaterEqual(self.d["inscripciones"]["vinculos_hechos_al_inscribir"], 1)

    def test_cuenta_los_casos_de_revision_existentes(self):
        self.assertEqual(self.d["revisiones"], {"abiertas": 1, "resueltas": 0})

    def test_proyeccion_de_la_migracion(self):
        p = self.d["proyeccion"]
        self.assertEqual(p["identidades_a_marcar_en_revision"], 2)
        self.assertGreaterEqual(p["personas_nuevas_sin_cuenta_que_se_crearian"], 2)  # 40111222 y los 5500000x
        self.assertGreaterEqual(p["vinculos_seguros_por_cuenta_enlazada"], 3)

    def test_los_hallazgos_distinguen_decision_humana_de_informativo(self):
        por_codigo = {h["codigo"]: h["severidad"] for h in self.d["hallazgos"]}
        for codigo in ("identidades_duplicadas", "inscripciones_dni_ambiguo", "coincidencia_nombre_dudosa",
                       "mismo_dni_nombres_distintos", "vinculos_retroactivos_sin_verificar",
                       "cuenta_vs_dni_inconsistente"):
            self.assertEqual(por_codigo[codigo], "decision_humana", codigo)
        for codigo in ("dni_sin_dni_valido", "dni_sospechosos", "dni_no_canonicos", "personas_a_crear"):
            self.assertEqual(por_codigo[codigo], "informativo", codigo)

    def test_no_hay_migraciones_previas_pendientes(self):
        self.assertEqual(migraciones_pendientes(), [])
        self.assertEqual(self.d["migraciones_pendientes"], [])


class PreflightAnonimizadoTests(TestCase):
    def setUp(self):
        self.ana = _con_nombre("27555000", "Ana", "Gómez", 6)
        self.ana.username = "anagomez"
        self.ana.save()
        _con_nombre("27.555.000", "Ana", "Dup", 5)
        crear_inscripcion(crear_categoria(), dni_1="55123456", nombre_1="Beatriz Quiroga",
                          dni_2="27555000", nombre_2="Mariano López")

    def test_por_defecto_no_aparece_ningun_dato_personal(self):
        salida = json.dumps(ejecutar_preflight(), ensure_ascii=False)
        for secreto in ("27555000", "27.555.000", "55123456", "Gómez", "Quiroga", "López", "anagomez"):
            self.assertNotIn(secreto, salida)
        self.assertIn("***000", salida)  # solo los últimos 3 dígitos

    def test_el_modo_completo_si_los_muestra(self):
        salida = json.dumps(ejecutar_preflight(anonimizar=False), ensure_ascii=False)
        self.assertIn("anagomez", salida)
        self.assertIn("Mariano López", salida)

    def test_el_comando_por_defecto_tampoco_los_muestra(self):
        out = StringIO()
        call_command("preflight_identidad", stdout=out)
        texto = out.getvalue()
        for secreto in ("27555000", "55123456", "Quiroga", "anagomez"):
            self.assertNotIn(secreto, texto)
        self.assertIn("ENMASCARADOS", texto)


class PreflightSoloLecturaTests(TestCase):
    def setUp(self):
        a = _con_nombre("27.555.000", "Ana", "Gómez", 6)
        b = _con_nombre("27555000", "Ana", "Dup", 5)
        cat = crear_categoria("6ta LIBRE")
        crear_inscripcion(cat, usuario=a, dni_2="27555000", nombre_2="Otra Persona")
        crear_inscripcion(cat, dni_2="40111222", nombre_2="Laura Paz")
        registrar_revision_dni("27555000", [a, b])

    def _foto(self):
        modelos = (User, Identidad, Jugador, Organizador, Torneo, Categoria, Inscripcion, RevisionIdentidad)
        return {m.__name__: list(m.objects.order_by("pk").values()) for m in modelos}

    def test_no_modifica_ninguna_tabla(self):
        antes = self._foto()
        for argumentos in ([], ["--completo"], ["--json"], ["--detalle", "0"]):
            call_command("preflight_identidad", *argumentos, stdout=StringIO())
        self.assertEqual(self._foto(), antes)

    def test_no_emite_ninguna_sentencia_de_escritura(self):
        with CaptureQueriesContext(connection) as consultas:
            call_command("preflight_identidad", "--completo", stdout=StringIO())
        self.assertGreater(len(consultas), 0)
        escrituras = [q["sql"] for q in consultas if q["sql"].lstrip().upper().startswith(ESCRITURAS)]
        self.assertEqual(escrituras, [])

    def test_el_resguardo_corta_cualquier_intento_de_escribir(self):
        ejecutado = []
        def execute(sql, params, many, context):
            ejecutado.append(sql)
        for sql in ("INSERT INTO x VALUES (1)", "  update x set a=1", "DELETE FROM x", "DROP TABLE x",
                    "ALTER TABLE x ADD y int", "CREATE TABLE x (a int)"):
            with self.assertRaises(EscrituraProhibida):
                _solo_lectura(execute, sql, (), False, {})
        _solo_lectura(execute, "SELECT 1", (), False, {})
        self.assertEqual(ejecutado, ["SELECT 1"])

    def test_la_salida_json_es_valida_y_completa(self):
        out = StringIO()
        call_command("preflight_identidad", "--json", stdout=out)
        datos = json.loads(out.getvalue())
        for clave in ("resumen", "identidades", "inscripciones", "proyeccion", "hallazgos", "revisiones"):
            self.assertIn(clave, datos)
        self.assertTrue(datos["anonimizado"])

    def test_el_informe_de_texto_tiene_todas_las_secciones_y_veredicto(self):
        out = StringIO()
        call_command("preflight_identidad", stdout=out)
        texto = out.getvalue()
        for parte in ("solo lectura (no se modificó nada)", "0. Migraciones", "1. Resumen", "2. Hallazgos",
                      "3. Detalle", "4. Qué haría la migración", "5. Veredicto", "solo aditivas"):
            self.assertIn(parte, texto)
        self.assertIn("DECISIÓN HUMANA", texto)

    def test_el_limite_de_detalle_se_respeta(self):
        for i in range(8):  # 8 identidades con DNI sospechoso (largo inusual) -> una lista de 8 casos
            _con_nombre(f"1{i}", "X", "Y")
        corto, largo = StringIO(), StringIO()
        call_command("preflight_identidad", "--detalle", "2", stdout=corto)
        call_command("preflight_identidad", "--detalle", "0", stdout=largo)
        self.assertIn("y 6 más", corto.getvalue())
        self.assertNotIn("más (usá", largo.getvalue())

    def test_funciona_con_la_base_vacia(self):
        Inscripcion.objects.all().delete()
        RevisionIdentidad.objects.all().delete()
        Identidad.objects.all().delete()
        out = StringIO()
        call_command("preflight_identidad", stdout=out)
        self.assertIn("Ninguno.", out.getvalue())
        self.assertIn("No hay casos que requieran decisión humana", out.getvalue())


class PreflightMigracionesTests(TestCase):
    def _con_pendientes(self, pendientes):
        from unittest import mock

        with mock.patch("torneos.preflight.migraciones_pendientes", return_value=pendientes):
            return ejecutar_preflight()

    def test_las_migraciones_de_esta_entrega_se_informan_pero_no_bloquean(self):
        d = self._con_pendientes(["accounts.0008_identidad_persona_reclamos_membresias", "rankings.0001_initial"])
        self.assertEqual(d["migraciones_pendientes"], [])
        self.assertEqual(len(d["migraciones_a_aplicar"]), 2)

    def test_cualquier_otra_pendiente_si_bloquea(self):
        d = self._con_pendientes(["torneos.0010_categoria_etapa_categoria_grupos_publicacion_and_more"])
        self.assertEqual(d["migraciones_pendientes"], ["torneos.0010_categoria_etapa_categoria_grupos_publicacion_and_more"])
        from unittest import mock

        out = StringIO()
        with mock.patch("torneos.preflight.migraciones_pendientes", return_value=d["migraciones_pendientes"]):
            call_command("preflight_identidad", stdout=out)
        self.assertIn("NO MIGRAR TODAVÍA", out.getvalue())

    def test_no_lee_columnas_que_solo_existen_despues_de_migrar(self):
        """El preflight corre ANTES de migrar: no puede seleccionar las columnas nuevas."""
        with CaptureQueriesContext(connection) as consultas:
            ejecutar_preflight()
        sql = " ".join(q["sql"] for q in consultas)
        for columna in ("dni_normalizado", "dni_en_revision", "persona_1_id", "persona_2_id", "apellido_1", "apellido_2", "permite_multiples_categorias"):
            self.assertNotIn(columna, sql, columna)
