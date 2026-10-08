from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.models.query import QuerySet
from django.test import Client, TestCase

from accounts.models import Identidad

from .models import Inscripcion
from .tests_participaciones import cliente, errores, inscribir, jugador, torneo_con_categorias
from .testing import crear_usuario

User = get_user_model()


def post_elegido(yo, categoria, identidad_id, **extra):
    """Inscripción eligiendo al compañero de la lista (solo manda su identificador)."""
    datos = {
        "categoria": categoria.id, "nombre_1": yo.first_name, "apellido_1": yo.last_name, "dni_1": yo.identidad.dni,
        "celular_1": "1", "localidad_1": "Tandil", "categoria_oficial_1": 5, "companero_id": identidad_id,
        "nombre_2": "", "apellido_2": "", "dni_2": "", "localidad_2": "", "categoria_oficial_2": "",
        "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": "",
    }
    datos.update(extra)
    return cliente(yo).post(f"/torneos/{categoria.torneo.codigo}/", datos)


class BuscarCompanerosTests(TestCase):
    URL = "/torneos/companeros/buscar/"

    def setUp(self):
        cache.clear()
        self.yo = jugador("30100001", "Ana", "Gómez")
        self.beto = jugador("27555000", "Beto", "Ruiz")
        Identidad.objects.filter(usuario=self.beto).update(localidad="Necochea")
        self.c = cliente(self.yo)

    def buscar(self, q):
        return self.c.get(self.URL, {"q": q}).json()["resultados"]

    def test_requiere_sesion_y_un_minimo_de_caracteres(self):
        self.assertEqual(Client().get(self.URL, {"q": "Beto"}).status_code, 401)
        self.assertEqual(self.buscar("Be"), [])

    def test_encuentra_por_nombre_y_apellido_sin_importar_tildes_ni_orden(self):
        for q in ("Beto Ruiz", "ruiz beto", "RUIZ", "bet"):
            self.assertEqual([r["apellido"] for r in self.buscar(q)], ["Ruiz"], q)
        jugador("29111000", "Carla", "Gómez Paz")
        self.assertEqual({r["nombre"] for r in self.buscar("gomez")}, {"Carla"})   # "Gómez" con tilde, buscado sin tilde

    def test_encuentra_por_dni_exacto_aunque_tenga_puntos(self):
        self.assertEqual([r["nombre"] for r in self.buscar("27.555.000")], ["Beto"])
        self.assertEqual(self.buscar("27555001"), [])

    def test_nunca_devuelve_el_dni_de_nadie(self):
        for q in ("Beto", "27555000"):
            texto = self.c.get(self.URL, {"q": q}).content.decode()
            self.assertNotIn("27555000", texto)
            self.assertNotIn('"dni"', texto)
        self.assertEqual(set(self.buscar("Beto")[0]), {"id", "nombre", "apellido", "localidad", "categoria_oficial"})

    def test_los_homonimos_se_muestran_todos_para_elegir_sin_asumir_ninguno(self):
        a = jugador("29111111", "Juan", "Pérez")
        b = jugador("29222222", "Juan", "Pérez")
        Identidad.objects.filter(usuario=a).update(localidad="Tandil")
        Identidad.objects.filter(usuario=b).update(localidad="Rauch")
        r = self.buscar("juan perez")
        self.assertEqual(sorted(x["localidad"] for x in r), ["Rauch", "Tandil"])
        self.assertEqual(len({x["id"] for x in r}), 2)

    def test_no_aparecen_uno_mismo_ni_personas_sin_cuenta_ni_ambiguos_ni_cuentas_sin_nombre_completo(self):
        Identidad.objects.create(usuario=None, dni="29555111", nombre="Beto Sin Cuenta", origen="inscripcion")
        ambiguo = jugador("28000111", "Beto", "Ambiguo")
        Identidad.objects.filter(usuario=ambiguo).update(dni_en_revision=True)
        crear_usuario(dni="28000222", categoria_oficial=5)          # sin nombre ni apellido
        incompleto = jugador("28000333", "Beto", "")                 # solo nombre
        r = self.buscar("beto")
        self.assertEqual([x["apellido"] for x in r], ["Ruiz"])
        self.assertEqual(self.buscar("ana"), [])                     # él mismo no se ofrece

    def test_devuelve_hasta_8_y_tiene_limite_de_consultas(self):
        for i in range(10):
            jugador(f"2{i:07d}", "Marta", f"Díaz{i}")
        self.assertEqual(len(self.buscar("marta")), 8)
        cache.clear()
        codigos = [self.c.get(self.URL, {"q": "marta"}).status_code for _ in range(62)]
        self.assertEqual(codigos[:60], [200] * 60)
        self.assertEqual(codigos[60:], [429, 429])


class ElegirDeLaListaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.yo = jugador("30100001", "Ana", "Gómez")
        self.beto = jugador("27555000", "Beto", "Ruiz")
        Identidad.objects.filter(usuario=self.beto).update(localidad="Necochea")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()

    def test_con_solo_el_identificador_se_completan_todos_los_datos_del_sistema(self):
        r = post_elegido(self.yo, self.c5, self.beto.identidad.pk)
        self.assertEqual(r.status_code, 302, errores(r, "companero_id"))
        i = Inscripcion.objects.get()
        self.assertEqual((i.nombre_2, i.apellido_2, i.localidad_2, i.dni_2, i.categoria_oficial_2),
                         ("Beto", "Ruiz", "Necochea", "27555000", 5))
        self.assertEqual((i.persona_2, i.usuario_2), (self.beto.identidad, self.beto))

    def test_lo_que_mande_el_navegador_en_los_campos_del_companero_se_ignora(self):
        post_elegido(self.yo, self.c5, self.beto.identidad.pk, nombre_2="Falso", apellido_2="Inventado",
                     localidad_2="Nunca", dni_2="11111111", categoria_oficial_2=8)
        i = Inscripcion.objects.get()
        self.assertEqual((i.nombre_completo_2, i.dni_2, i.categoria_oficial_2), ("Beto Ruiz", "27555000", 5))
        self.beto.jugador.refresh_from_db()
        self.assertEqual(self.beto.jugador.categoria_oficial, 5)   # su categoría oficial nadie la toca

    def test_si_todavia_no_tiene_categoria_oficial_se_declara_para_la_inscripcion_sin_guardarla_en_su_perfil(self):
        sin_cat = jugador("28111000", "Dora", "Vega", categoria=None)
        r = post_elegido(self.yo, self.c5, sin_cat.identidad.pk)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(errores(r, "categoria_oficial_2"))
        r = post_elegido(self.yo, self.c5, sin_cat.identidad.pk, categoria_oficial_2=5)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Inscripcion.objects.get().categoria_oficial_2, 5)
        sin_cat.jugador.refresh_from_db()
        self.assertIsNone(sin_cat.jugador.categoria_oficial)       # pendiente de que ella la confirme

    def test_identificadores_invalidos_se_rechazan(self):
        sin_cuenta = Identidad.objects.create(usuario=None, dni="29555111", nombre="Laura Paz", origen="inscripcion")
        en_revision = jugador("28000111", "Beto", "Ambiguo")
        Identidad.objects.filter(usuario=en_revision).update(dni_en_revision=True)
        sin_apellido = jugador("28000333", "Beto", "")
        for nombre, pk in (("inexistente", 99999), ("uno mismo", self.yo.identidad.pk), ("persona sin cuenta", sin_cuenta.pk),
                           ("DNI en revisión", en_revision.identidad.pk), ("sin nombre completo", sin_apellido.identidad.pk)):
            with self.subTest(nombre):
                r = post_elegido(self.yo, self.c5, pk)
                self.assertEqual(r.status_code, 200)
                self.assertTrue(errores(r, "companero_id"))
        self.assertEqual(Inscripcion.objects.count(), 0)

    def test_sin_compañero_elegido_ni_dni_se_pide_uno_de_los_dos(self):
        r = post_elegido(self.yo, self.c5, "")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(errores(r, "dni_2"))

    def test_el_alta_manual_sigue_funcionando_para_quien_no_esta_en_la_plataforma(self):
        r = inscribir(self.yo, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29555111", localidad_2="Tandil")
        self.assertEqual(r.status_code, 302)
        laura = Identidad.objects.get(dni="29555111")
        self.assertIsNone(laura.usuario)                           # persona sin cuenta, lista para reclamar su historial
        self.assertEqual(Inscripcion.objects.get().persona_2, laura)

    def test_las_reglas_de_participacion_valen_tambien_para_el_compañero_elegido(self):
        otro = jugador("29111000", "Carla", "Paz")
        post_elegido(otro, self.c5, self.beto.identidad.pk)
        r = post_elegido(self.yo, self.c5, self.beto.identidad.pk)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Beto Ruiz ya está inscripto en 5ta LIBRE.", errores(r))

    def test_el_formulario_conserva_al_elegido_si_vuelve_con_errores(self):
        r = post_elegido(self.yo, self.c5, self.beto.identidad.pk, celular_1="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["form"].companero_elegido["apellido"], "Ruiz")
        self.assertContains(r, 'data-nombre="Beto"')

    def test_la_pagina_trae_el_buscador(self):
        pagina = cliente(self.yo).get(f"/torneos/{self.t.codigo}/")
        for marca in ("gpCompBuscar", "gpCompaneroId", "/torneos/companeros/buscar/", "gpCompManualCampos"):
            self.assertContains(pagina, marca)


class CanceladasYSimultaneasTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a = jugador("30100001", "Ana", "Gómez")
        self.b = jugador("27555000", "Beto", "Ruiz")
        self.c = jugador("29111000", "Carla", "Paz")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()

    def test_vigente_es_pendiente_o_confirmada(self):
        self.assertEqual(set(Inscripcion.ESTADOS_NO_VIGENTES), {"rechazada", "cancelada"})
        self.assertIn("cancelada", dict(Inscripcion.ESTADO_CHOICES))

    def test_una_inscripcion_cancelada_o_rechazada_libera_a_la_pareja(self):
        for estado in (Inscripcion.ESTADO_CANCELADA, Inscripcion.ESTADO_RECHAZADA):
            with self.subTest(estado):
                Inscripcion.objects.all().delete()
                inscribir(self.a, self.c5, self.b)
                Inscripcion.objects.update(estado=estado)
                self.assertEqual(inscribir(self.a, self.c5, self.b).status_code, 302)   # la misma pareja, de nuevo

    def test_pendiente_y_confirmada_si_ocupan_lugar(self):
        for estado in (Inscripcion.ESTADO_PENDIENTE, Inscripcion.ESTADO_CONFIRMADA):
            with self.subTest(estado):
                Inscripcion.objects.all().delete()
                inscribir(self.a, self.c5, self.b)
                Inscripcion.objects.update(estado=estado)
                self.assertEqual(inscribir(self.a, self.c5, self.c).status_code, 200)

    def test_una_cancelada_no_muestra_INSCRIPTO_pero_sigue_en_mis_inscripciones(self):
        inscribir(self.a, self.c5, self.b)
        Inscripcion.objects.update(estado=Inscripcion.ESTADO_CANCELADA)
        self.assertNotContains(cliente(self.a).get("/torneos/"), "data-inscripto-badge")
        mis = cliente(self.a).get("/mis-inscripciones/")
        self.assertEqual(len(mis.context["inscripciones"]), 1)
        self.assertContains(mis, "Cancelada")

    def test_el_doble_clic_no_duplica_la_inscripcion(self):
        self.assertEqual(inscribir(self.a, self.c5, self.b).status_code, 302)
        self.assertEqual(inscribir(self.a, self.c5, self.b).status_code, 200)   # el segundo envío idéntico
        self.assertEqual(Inscripcion.objects.count(), 1)

    def test_validar_y_guardar_ocurren_juntos_bajo_un_bloqueo_del_torneo(self):
        original = QuerySet.select_for_update
        llamadas = []

        def espia(qs, *args, **kwargs):
            llamadas.append(connection.in_atomic_block)
            return original(qs, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", espia):
            inscribir(self.a, self.c5, self.b)
        self.assertTrue(llamadas and all(llamadas), llamadas)   # se bloquea el torneo DENTRO de la transacción


class MisInscripcionesParaAmbosTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a = jugador("30100001", "Ana", "Gómez")
        self.b = jugador("27555000", "Beto", "Ruiz")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()

    def test_ambos_ven_la_misma_inscripcion_sin_duplicarla_en_la_base(self):
        inscribir(self.a, self.c5, self.b, disponibilidad="Viernes de 18:00 a 22:00")
        self.assertEqual(Inscripcion.objects.count(), 1)
        a_ve = cliente(self.a).get("/mis-inscripciones/").context["inscripciones"]
        b_ve = cliente(self.b).get("/mis-inscripciones/").context["inscripciones"]
        self.assertEqual([i.pk for i in a_ve], [i.pk for i in b_ve])

    def test_muestran_torneo_categoria_pareja_estado_y_disponibilidad(self):
        inscribir(self.a, self.c5, self.b, disponibilidad="Viernes de 18:00 a 22:00")
        for quien, pareja in ((self.a, "Con Beto Ruiz"), (self.b, "Con Ana Gómez")):
            pagina = cliente(quien).get("/mis-inscripciones/")
            for dato in (self.t.nombre, "5ta LIBRE", pareja, "Pendiente", "Viernes de 18:00 a 22:00"):
                self.assertContains(pagina, dato)

    def test_sin_disponibilidad_declarada_se_entiende_que_pueden_siempre(self):
        inscribir(self.a, self.c5, self.b)
        self.assertContains(cliente(self.b).get("/mis-inscripciones/"), "todos los días y horarios")
