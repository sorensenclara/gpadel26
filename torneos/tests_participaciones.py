from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase

from accounts.models import Identidad

from .forms import InscripcionForm, TorneoForm
from .models import Categoria, Inscripcion, Torneo
from .participaciones import Participante, categorias_disponibles, inscripciones_de, validar_participacion
from .testing import crear_categoria, crear_usuario

User = get_user_model()


def jugador(dni, nombre, apellido, categoria=5):
    u = crear_usuario(dni=dni, categoria_oficial=categoria)
    u.first_name, u.last_name = nombre, apellido
    u.save()
    return u


def cliente(usuario):
    c = Client()
    c.login(username=usuario.username, password="x")
    return c


def torneo_con_categorias(multiples=False, subir=1):
    """Torneo por categoría con 4ta, 5ta y 6ta."""
    c5 = crear_categoria("5ta LIBRE")
    t = c5.torneo
    t.permite_multiples_categorias, t.categorias_permitir_subir = multiples, subir
    t.save()
    c4 = Categoria.objects.create(torneo=t, nombre="4ta LIBRE", cupo_minimo=1)
    c6 = Categoria.objects.create(torneo=t, nombre="6ta LIBRE", cupo_minimo=1)
    return t, c4, c5, c6


def inscribir(yo, categoria, companero=None, nivel_1=5, nivel_2=5, **extra):
    """Inscribe a `yo` con `companero` (cuenta de la plataforma) o con los datos tipeados en `extra`."""
    datos = {
        "categoria": categoria.id, "nombre_1": yo.first_name or "Yo", "apellido_1": yo.last_name or "Mismo",
        "dni_1": yo.identidad.dni, "celular_1": "1", "localidad_1": "Tandil", "categoria_oficial_1": nivel_1,
        "nombre_2": "", "apellido_2": "", "dni_2": "", "localidad_2": "", "categoria_oficial_2": nivel_2,
        "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": "",
    }
    if companero is not None:
        datos["dni_2"] = companero.identidad.dni
    datos.update(extra)
    return cliente(yo).post(f"/torneos/{categoria.torneo.codigo}/", datos)


def errores(respuesta, campo="categoria"):
    if respuesta.context is None:  # una redirección: no hay formulario con errores
        return []
    return respuesta.context["form"].errors.get(campo, [])


class NombreYApellidoTests(TestCase):
    def setUp(self):
        self.a = jugador("30100001", "Ana", "Gómez")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()

    def test_el_nombre_y_el_apellido_son_campos_separados_y_obligatorios_para_el_jugador_1(self):
        r = inscribir(self.a, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29111222", localidad_2="Tandil", apellido_1="")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(errores(r, "apellido_1"))

    def test_el_compañero_fuera_de_la_plataforma_exige_nombre_apellido_y_localidad(self):
        r = inscribir(self.a, self.c5, dni_2="29111222")
        self.assertEqual(r.status_code, 200)
        for campo in ("nombre_2", "apellido_2", "localidad_2"):
            self.assertTrue(errores(r, campo), campo)
        self.assertEqual(Inscripcion.objects.count(), 0)

    def test_se_guardan_nombre_y_apellido_por_separado_y_la_persona_sin_cuenta_con_el_nombre_completo(self):
        r = inscribir(self.a, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29111222", localidad_2="Necochea")
        self.assertEqual(r.status_code, 302)
        insc = Inscripcion.objects.get()
        self.assertEqual((insc.nombre_1, insc.apellido_1, insc.nombre_2, insc.apellido_2), ("Ana", "Gómez", "Laura", "Paz"))
        self.assertEqual((insc.nombre_completo_1, insc.nombre_completo_2), ("Ana Gómez", "Laura Paz"))
        self.assertEqual(Identidad.objects.get(dni="29111222").nombre, "Laura Paz")

    def test_las_inscripciones_anteriores_con_apellido_y_nombre_juntos_se_siguen_mostrando(self):
        vieja = Inscripcion.objects.create(
            torneo=self.t, categoria=self.c5, nombre_1="Gómez Ana", dni_1="1", localidad_1="T", nombre_2="Paz Laura",
            dni_2="2", localidad_2="T", telefono="1",
        )
        self.assertEqual((vieja.apellido_1, vieja.nombre_completo_1, vieja.nombre_completo_2), ("", "Gómez Ana", "Paz Laura"))
        self.assertIn("Gómez Ana / Paz Laura", str(vieja))

    def test_el_formulario_precarga_nombre_y_apellido_de_la_cuenta(self):
        form = InscripcionForm(torneo=self.t, usuario=self.a)
        self.assertEqual((form.fields["nombre_1"].initial, form.fields["apellido_1"].initial), ("Ana", "Gómez"))

    def test_si_el_jugador_1_corrige_su_nombre_se_actualiza_su_cuenta(self):
        inscribir(self.a, self.c5, nombre_1="Anita", apellido_1="Gómez Ruiz", nombre_2="Laura", apellido_2="Paz",
                  dni_2="29111222", localidad_2="Tandil")
        self.a.refresh_from_db()
        self.assertEqual((self.a.first_name, self.a.last_name), ("Anita", "Gómez Ruiz"))


class CompaneroEnLaPlataformaTests(TestCase):
    """Si ya está en GPADEL con su nombre, lo pone el sistema: no hay nada más que completar."""

    def setUp(self):
        self.a = jugador("30100001", "Ana", "Gómez")
        self.b = jugador("27555000", "Beto", "Ruiz")
        Identidad.objects.filter(usuario=self.b).update(localidad="Necochea")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()
        cache.clear()

    def test_se_completan_nombre_apellido_y_localidad_con_los_datos_del_sistema_aunque_el_navegador_mande_otros(self):
        r = inscribir(self.a, self.c5, self.b, nombre_2="Cualquiera", apellido_2="Inventado", localidad_2="Otra parte")
        self.assertEqual(r.status_code, 302)
        insc = Inscripcion.objects.get()
        self.assertEqual((insc.nombre_2, insc.apellido_2, insc.localidad_2), ("Beto", "Ruiz", "Necochea"))

    def test_no_hace_falta_enviar_nada_mas_que_el_dni(self):
        r = inscribir(self.a, self.c5, self.b)   # nombre, apellido y localidad vacíos
        self.assertEqual(r.status_code, 302, errores(r, "nombre_2"))
        insc = Inscripcion.objects.get()
        self.assertEqual(insc.nombre_completo_2, "Beto Ruiz")
        self.assertEqual((insc.persona_2, insc.usuario_2), (self.b.identidad, self.b))   # y queda vinculado

    def test_la_consulta_devuelve_los_datos_solo_de_quien_ya_esta_en_la_plataforma(self):
        c = cliente(self.a)
        self.assertEqual(c.get("/torneos/companero/", {"dni": "27.555.000"}).json(),
                         {"tiene_cuenta": True, "categoria_oficial": 5, "bloqueada": True,
                          "nombre": "Beto", "apellido": "Ruiz", "localidad": "Necochea"})

    def test_la_consulta_no_revela_nada_de_personas_sin_cuenta_dni_ambiguos_ni_desconocidos(self):
        Identidad.objects.create(usuario=None, dni="29111222", nombre="Laura Paz", origen="inscripcion")
        c = cliente(self.a)
        for dni in ("29111222", "99999999"):
            d = c.get("/torneos/companero/", {"dni": dni}).json()
            self.assertEqual(set(d), {"tiene_cuenta", "categoria_oficial", "bloqueada"}, dni)
            self.assertFalse(d["tiene_cuenta"])
        Identidad.objects.create(usuario=None, dni="27.555.000", nombre="Otro Beto", dni_en_revision=True)
        Identidad.objects.filter(usuario=self.b).update(dni_en_revision=True)
        d = c.get("/torneos/companero/", {"dni": "27555000"}).json()
        self.assertTrue(d["ambiguo"])
        self.assertNotIn("nombre", d)

    def test_una_cuenta_sin_nombre_completo_no_expone_ni_autocompleta_nada(self):
        sin_nombre = crear_usuario(dni="31000111", categoria_oficial=5)
        d = cliente(self.a).get("/torneos/companero/", {"dni": "31000111"}).json()
        self.assertNotIn("nombre", d)

    def test_la_consulta_tiene_un_limite_por_minuto(self):
        c = cliente(self.a)
        codigos = [c.get("/torneos/companero/", {"dni": f"3{i:07d}"}).status_code for i in range(32)]
        self.assertEqual(codigos[:30], [200] * 30)
        self.assertEqual(codigos[30:], [429, 429])
        self.assertEqual(cliente(self.b).get("/torneos/companero/", {"dni": "30100001"}).status_code, 200)  # por cuenta


class ReglasDeParticipacionTests(TestCase):
    def setUp(self):
        self.a = jugador("30100001", "Ana", "Gómez")
        self.b = jugador("27555000", "Beto", "Ruiz")
        self.c = jugador("29111000", "Carla", "Paz")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias(multiples=False)
        cache.clear()

    def _habilitar(self):
        self.t.permite_multiples_categorias = True
        self.t.save()

    def test_no_se_puede_volver_a_inscribir_en_la_misma_categoria_ni_siquiera_con_otra_pareja(self):
        self.assertEqual(inscribir(self.a, self.c5, self.b).status_code, 302)
        self._habilitar()   # aun habilitado, la misma categoría nunca
        r = inscribir(self.a, self.c5, self.c)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ya estás inscripto en 5ta LIBRE.", errores(r))
        self.assertEqual(Inscripcion.objects.count(), 1)

    def test_el_compañero_tampoco_puede_estar_dos_veces_en_la_misma_categoria(self):
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.c, self.c5, self.b)   # Carla intenta anotarse con Beto, que ya está en 5ta
        self.assertEqual(r.status_code, 200)
        self.assertIn("Beto Ruiz ya está inscripto en 5ta LIBRE.", errores(r))

    def test_sin_habilitacion_del_organizador_no_se_puede_jugar_otra_categoria(self):
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.a, self.c4, self.c)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("El organizador no habilitó" in e for e in errores(r)))
        self.assertEqual(Inscripcion.objects.count(), 1)

    def test_habilitado_se_puede_sumar_una_categoria_mas_alta_con_otra_pareja(self):
        self._habilitar()
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.a, self.c4, self.c)
        self.assertEqual(r.status_code, 302, errores(r))
        self.assertEqual(Inscripcion.objects.filter(torneo=self.t).count(), 2)

    def test_nunca_la_misma_pareja_en_dos_categorias(self):
        self._habilitar()
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.a, self.c4, self.b)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("ya forman pareja en 5ta LIBRE" in e for e in errores(r)))
        r2 = inscribir(self.b, self.c4, self.a)     # y tampoco invirtiendo quién inscribe
        self.assertTrue(any("ya forman pareja" in e for e in errores(r2)))

    def test_no_se_puede_sumar_una_categoria_mas_baja(self):
        self._habilitar()
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.a, self.c6, self.c)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("solo se pueden sumar categorías más altas" in e for e in errores(r)))

    def test_una_inscripcion_rechazada_no_cuenta(self):
        inscribir(self.a, self.c5, self.b)
        Inscripcion.objects.update(estado=Inscripcion.ESTADO_RECHAZADA)
        self.assertEqual(inscribir(self.a, self.c5, self.c).status_code, 302)

    def test_la_regla_vale_igual_si_a_la_persona_la_inscribieron_sin_cuenta_y_se_la_nombra_por_dni(self):
        """Laura no está en la plataforma: se la reconoce por su DNI."""
        inscribir(self.a, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29555111", localidad_2="Tandil")
        r = inscribir(self.c, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29.555.111", localidad_2="Tandil")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Laura Paz ya está inscripto en 5ta LIBRE.", errores(r))

    def test_en_un_torneo_por_sumatoria_nunca_hay_una_segunda_categoria(self):
        suma = jugador("32000111", "Sofía", "Lima", 6)
        otro = jugador("32000222", "Mario", "Sosa", 7)
        tercero = jugador("32000333", "Lucas", "Vega", 8)
        cat = crear_categoria("1ra LIBRE", sumatoria_minima=13)
        cat.torneo.modalidad_categoria, cat.torneo.sumatoria_valor = Torneo.MODALIDAD_POR_SUMATORIA, 13
        cat.torneo.permite_multiples_categorias = True
        cat.torneo.save()
        self.assertEqual(inscribir(suma, cat, otro, nivel_1=6, nivel_2=7).status_code, 302)
        r = inscribir(suma, cat, tercero, nivel_1=6, nivel_2=8)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(errores(r))

    def test_en_otro_torneo_no_hay_restriccion(self):
        inscribir(self.a, self.c5, self.b)
        otro_torneo, _, o5, _ = torneo_con_categorias()
        self.assertEqual(inscribir(self.a, o5, self.b).status_code, 302)

    def test_el_formulario_solo_ofrece_las_categorias_que_siguen_disponibles(self):
        self._habilitar()
        inscribir(self.a, self.c5, self.b)
        form = InscripcionForm(torneo=self.t, usuario=self.a)
        self.assertEqual([c.nombre for c in form.fields["categoria"].queryset], ["4ta LIBRE"])
        self.assertEqual([c.nombre for c in categorias_disponibles(self.t, list(inscripciones_de(self.a)))], ["4ta LIBRE"])

    def test_la_validacion_es_de_backend_aunque_se_envie_una_categoria_que_el_formulario_no_ofrecia(self):
        inscribir(self.a, self.c5, self.b)
        r = inscribir(self.a, self.c5, self.c)       # POST armado a mano con la categoría ya tomada
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Inscripcion.objects.filter(torneo=self.t).count(), 1)

    def test_validar_participacion_por_si_sola(self):
        inscribir(self.a, self.c5, self.b)
        yo = Participante("Ana Gómez", self.a.pk, self.a.identidad.pk, frozenset({"30100001"}), es_el_usuario=True)
        otro = Participante("Carla Paz", self.c.pk, self.c.identidad.pk, frozenset({"29111000"}))
        self.assertTrue(validar_participacion(self.t, self.c5, [yo, otro]))
        self.assertEqual(validar_participacion(Torneo.objects.get(pk=self.t.pk), self.c4, [otro, Participante("Zeta", dnis=frozenset({"1"}))]), [])


class InscriptoEnLaListaYEnMisInscripcionesTests(TestCase):
    def setUp(self):
        self.a = jugador("30100001", "Ana", "Gómez")
        self.b = jugador("27555000", "Beto", "Ruiz")
        self.c = jugador("29111000", "Carla", "Paz")
        self.t, self.c4, self.c5, self.c6 = torneo_con_categorias()
        cache.clear()
        inscribir(self.a, self.c5, self.b)

    def test_quien_se_inscribio_ve_INSCRIPTO_en_torneos(self):
        pagina = cliente(self.a).get("/torneos/")
        self.assertContains(pagina, "data-inscripto-badge")
        self.assertContains(pagina, "Ver mi inscripción")
        self.assertNotContains(pagina, 'gp-inscribirme-link">Inscribirme')

    def test_su_companero_registrado_tambien_ve_INSCRIPTO_y_la_inscripcion_en_mis_inscripciones(self):
        pagina = cliente(self.b).get("/torneos/")
        self.assertContains(pagina, "data-inscripto-badge")
        mis = cliente(self.b).get("/mis-inscripciones/")
        insc = mis.context["inscripciones"]
        self.assertEqual(len(insc), 1)
        self.assertEqual((insc[0].mi_lado, insc[0].mi_companero, insc[0].la_hizo_mi_pareja), (2, "Ana Gómez", True))
        self.assertContains(mis, "Con Ana Gómez")
        self.assertContains(mis, "te inscribió Ana Gómez")

    def test_quien_la_inscribio_ve_a_su_companero_como_pareja(self):
        mis = cliente(self.a).get("/mis-inscripciones/")
        self.assertEqual(mis.context["inscripciones"][0].mi_companero, "Beto Ruiz")
        self.assertFalse(mis.context["inscripciones"][0].la_hizo_mi_pareja)

    def test_los_demas_y_los_anonimos_no_ven_INSCRIPTO(self):
        self.assertNotContains(cliente(self.c).get("/torneos/"), "data-inscripto-badge")
        self.assertNotContains(Client().get("/torneos/"), "data-inscripto-badge")
        self.assertEqual(cliente(self.c).get("/mis-inscripciones/").context["inscripciones"], [])

    def test_si_el_organizador_habilita_otra_categoria_el_boton_lo_dice(self):
        self.t.permite_multiples_categorias = True
        self.t.save()
        pagina = cliente(self.a).get("/torneos/")
        self.assertContains(pagina, "Inscribirme en otra categoría")
        self.assertContains(pagina, "data-inscripto-badge")

    def test_la_pagina_del_torneo_reemplaza_el_formulario_por_INSCRIPTO_si_no_puede_sumar_otra(self):
        pagina = cliente(self.a).get(f"/torneos/{self.t.codigo}/")
        self.assertContains(pagina, "INSCRIPTO")
        self.assertContains(pagina, "no habilitó jugar más de una categoría")
        self.assertNotContains(pagina, 'id="gpInscForm"')

    def test_la_pagina_del_torneo_muestra_el_formulario_y_el_aviso_si_puede_sumar_otra(self):
        self.t.permite_multiples_categorias = True
        self.t.save()
        pagina = cliente(self.a).get(f"/torneos/{self.t.codigo}/")
        self.assertContains(pagina, 'id="gpInscForm"')
        self.assertContains(pagina, "más alta")
        self.assertContains(pagina, "otra pareja")

    def test_la_inscripcion_de_alguien_sin_cuenta_vinculada_no_le_aparece_a_otra_cuenta(self):
        inscribir(self.c, self.c5, nombre_2="Laura", apellido_2="Paz", dni_2="29555111", localidad_2="Tandil", nivel_1=5, nivel_2=5)
        self.assertEqual(len(inscripciones_de(self.b)), 1)   # solo la de Ana: la de Carla no la nombra
        self.assertEqual(len(inscripciones_de(self.c)), 1)

    def test_las_rechazadas_no_cuentan_como_INSCRIPTO_pero_se_ven_en_mis_inscripciones(self):
        Inscripcion.objects.update(estado=Inscripcion.ESTADO_RECHAZADA)
        self.assertNotContains(cliente(self.a).get("/torneos/"), "data-inscripto-badge")
        self.assertEqual(len(cliente(self.a).get("/mis-inscripciones/").context["inscripciones"]), 1)


class HabilitacionDelOrganizadorTests(TestCase):
    def test_por_defecto_no_esta_habilitado_y_el_formulario_del_torneo_lo_incluye(self):
        self.assertIn("permite_multiples_categorias", TorneoForm().fields)
        c = crear_categoria("6ta LIBRE")
        self.assertFalse(c.torneo.permite_multiples_categorias)

    def test_el_organizador_lo_puede_activar(self):
        c = crear_categoria("6ta LIBRE")
        form = TorneoForm(
            {"nombre": "T", "sede": "", "ciudad": "Tandil", "formato": "solo_eliminacion",
             "modalidad_categoria": "por_categoria", "categorias_permitir_subir": 0,
             "permite_multiples_categorias": "on",
             "fecha_inicio": c.torneo.fecha_inicio, "fecha_fin": c.torneo.fecha_fin,
             "fecha_limite_inscripcion": c.torneo.fecha_limite_inscripcion, "precio_inscripcion": "0"},
            instance=c.torneo,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().permite_multiples_categorias)
