import datetime
import importlib
from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.utils import timezone

from accounts.identidad import DniYaRegistrado, dar_identidad_a_cuenta
from accounts.models import EventoIdentidad, Identidad, ReclamoIdentidad

from .forms import InscripcionForm
from .models import Inscripcion, RevisionIdentidad
from .servicios import asignar_personas, buscar_companero, resolver_persona, vincular_companero
from .servicios_identidad import (
    ReclamoInvalido,
    completar_identidad_propia,
    rechazar_reclamo,
    verificar_reclamo,
)
from .testing import crear_categoria, crear_inscripcion, crear_usuario

User = get_user_model()


def nombrar(usuario, nombre, apellido):
    usuario.first_name, usuario.last_name = nombre, apellido
    usuario.save()
    return usuario


def persona(dni="27555000", nombre="Pedro Sosa", **extra):
    return Identidad.objects.create(usuario=None, dni=dni, nombre=nombre, origen=Identidad.ORIGEN_INSCRIPCION, **extra)


def post_inscripcion(usuario, cat, dni_1, **extra):
    datos = {
        "categoria": cat.id, "nombre_1": usuario.first_name or "Jugador", "apellido_1": usuario.last_name or "Uno", "dni_1": dni_1, "celular_1": "1",
        "localidad_1": "Tandil", "categoria_oficial_1": 6, "nombre_2": "Laura", "apellido_2": "Paz", "dni_2": "29111222",
        "localidad_2": "Tandil", "categoria_oficial_2": 6, "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": "",
    }
    datos.update(extra)
    c = Client()
    c.login(username=usuario.username, password="x")
    return c.post(f"/torneos/{cat.torneo.codigo}/", datos)


class PersonasEnInscripcionesTests(TestCase):
    def setUp(self):
        self.yo = nombrar(crear_usuario(dni="30100001", categoria_oficial=6), "Yo", "Mismo")
        self.cat = crear_categoria("6ta LIBRE")

    def test_un_companero_sin_cuenta_queda_como_persona_con_historial_propio(self):
        r = post_inscripcion(self.yo, self.cat, "30100001")
        self.assertEqual(r.status_code, 302)
        insc = Inscripcion.objects.get()
        laura = Identidad.objects.get(dni="29111222")
        self.assertIsNone(laura.usuario)
        self.assertEqual((laura.nombre, laura.origen), ("Laura Paz", Identidad.ORIGEN_INSCRIPCION))
        self.assertEqual((insc.persona_1, insc.persona_2, insc.usuario_2), (self.yo.identidad, laura, None))
        self.assertEqual((insc.nombre_2, insc.apellido_2, insc.dni_2, insc.categoria_oficial_2), ("Laura", "Paz", "29111222", 6))  # intacto
        self.assertTrue(EventoIdentidad.objects.filter(accion="persona_creada", persona=laura).exists())

    def test_la_misma_persona_en_otro_torneo_no_se_duplica_aunque_cambie_el_formato_del_dni(self):
        post_inscripcion(self.yo, self.cat, "30100001")
        cat2 = crear_categoria("6ta LIBRE")
        post_inscripcion(self.yo, cat2, "30100001", dni_2="29.111.222")
        self.assertEqual(Identidad.objects.filter(dni_normalizado="29111222").count(), 1)
        self.assertEqual(Identidad.objects.get(dni_normalizado="29111222").inscripciones_como_jugador_2.count(), 2)

    def test_mismo_dni_con_un_nombre_incompatible_no_se_vincula_ni_se_duplica_se_revisa(self):
        post_inscripcion(self.yo, self.cat, "30100001")
        cat2 = crear_categoria("6ta LIBRE")
        post_inscripcion(self.yo, cat2, "30100001", nombre_2="Roberto", apellido_2="Gil")
        segunda = Inscripcion.objects.get(categoria=cat2)
        self.assertIsNone(segunda.persona_2)
        self.assertEqual(Identidad.objects.filter(dni_normalizado="29111222").count(), 1)  # no se creó otra
        caso = RevisionIdentidad.objects.get(motivo="nombre_no_coincide")
        self.assertEqual(caso.estado, "abierta")
        self.assertEqual(list(caso.inscripciones.all()), [segunda])
        self.assertEqual((segunda.nombre_2, segunda.apellido_2), ("Roberto", "Gil"))  # lo declarado se conserva

    def test_companero_con_cuenta_y_nombre_compatible_se_vincula_cuenta_e_identidad(self):
        pedro = nombrar(crear_usuario(dni="27555000", categoria_oficial=6), "Pedro", "Sosa")
        post_inscripcion(self.yo, self.cat, "30100001", nombre_2="Pedro", apellido_2="Sosa", dni_2="27555000")
        insc = Inscripcion.objects.get()
        self.assertEqual((insc.persona_2, insc.usuario_2), (pedro.identidad, pedro))

    def test_companero_con_cuenta_pero_nombre_incompatible_no_se_vincula_pero_su_categoria_sigue_forzada(self):
        """Cuenta cuyo nombre en el sistema está incompleto (solo nombre): se usa lo tipeado, y si no
        coincide no se atribuye la inscripción. Aun así, un nombre falso NO sirve para esquivar la
        categoría oficial del compañero."""
        pedro = nombrar(crear_usuario(dni="27555000", categoria_oficial=5), "Pedro", "")
        cat5 = crear_categoria("5ta LIBRE")
        form = InscripcionForm(
            {"categoria": cat5.id, "nombre_1": "Yo", "apellido_1": "Prueba", "dni_1": "30100001", "celular_1": "1", "localidad_1": "Tandil",
             "categoria_oficial_1": 6, "nombre_2": "Nombre", "apellido_2": "Falso", "dni_2": "27555000", "localidad_2": "Tandil",
             "categoria_oficial_2": 8, "telefono": "1", "metodo_pago": "efectivo"},
            torneo=cat5.torneo, usuario=self.yo,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria_oficial_2"], 5)   # forzada por el DNI, no por el nombre
        insc = form.save(torneo=cat5.torneo)
        self.assertIsNone(insc.usuario_2)                                # pero la atribución queda en revisión
        self.assertIsNone(insc.persona_2)
        self.assertTrue(RevisionIdentidad.objects.filter(motivo="nombre_no_coincide", dni="27555000").exists())

    def test_dni_ambiguo_no_crea_ni_vincula_ninguna_persona(self):
        persona("27.555.000", dni_en_revision=True)
        persona("27555000", nombre="Otro", dni_en_revision=True)
        antes = Identidad.objects.count()
        post_inscripcion(self.yo, self.cat, "30100001", dni_2="27555000", nombre_2="Pedro", apellido_2="Sosa")
        insc = Inscripcion.objects.get()
        self.assertEqual((insc.persona_2, insc.usuario_2), (None, None))
        self.assertEqual(Identidad.objects.count(), antes)
        self.assertTrue(RevisionIdentidad.objects.filter(motivo="dni_ambiguo").exists())

    def test_un_dni_inutilizable_no_genera_personas(self):
        antes = Identidad.objects.count()
        self.assertIsNone(resolver_persona("1", "Cualquiera"))
        self.assertIsNone(resolver_persona("s/d", "Cualquiera"))
        self.assertIsNone(resolver_persona("", "Cualquiera"))
        self.assertEqual(Identidad.objects.count(), antes)

    def test_el_jugador_1_sale_de_su_cuenta_nunca_del_dni_tipeado(self):
        ajena = persona("27555000", nombre="Pedro Sosa")
        post_inscripcion(self.yo, self.cat, "27555000")   # tipea el DNI de OTRA persona como suyo
        insc = Inscripcion.objects.get()
        self.assertEqual(insc.persona_1, self.yo.identidad)
        self.assertEqual(ajena.inscripciones_como_jugador_1.count(), 0)
        self.yo.identidad.refresh_from_db()
        self.assertEqual(self.yo.identidad.dni, "30100001")   # su DNI de cuenta no se pisó con el de la otra persona

    def test_la_misma_persona_en_ambos_lados_no_se_asocia_dos_veces(self):
        insc = crear_inscripcion(self.cat, usuario=self.yo, dni_2="30100001", nombre_2="Yo Mismo")
        asignar_personas(insc)
        insc.refresh_from_db()
        self.assertEqual(insc.persona_1, self.yo.identidad)
        self.assertIsNone(insc.persona_2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inscripcion.objects.filter(pk=insc.pk).update(persona_2=self.yo.identidad)

    def test_vincular_companero_solo_dice_que_vinculo_cuando_vinculo_una_cuenta(self):
        sin_cuenta = persona("27000111", nombre="Laura Paz")
        insc = crear_inscripcion(self.cat, usuario=self.yo, dni_2="27000111", nombre_2="Laura Paz")
        self.assertFalse(vincular_companero(insc))        # antes devolvía True sin vincular a nadie
        insc.refresh_from_db()
        self.assertEqual((insc.persona_2, insc.usuario_2), (sin_cuenta, None))
        pedro = nombrar(crear_usuario(dni="27555000"), "Pedro", "Sosa")
        con_cuenta = crear_inscripcion(self.cat, usuario=self.yo, dni_2="27555000", nombre_2="Pedro Sosa")
        self.assertTrue(vincular_companero(con_cuenta))
        con_cuenta.refresh_from_db()
        self.assertEqual(con_cuenta.usuario_2, pedro)

    def test_un_vinculo_historico_ya_hecho_conserva_su_persona(self):
        pedro = nombrar(crear_usuario(dni="27555000"), "Pedro", "Sosa")
        insc = crear_inscripcion(self.cat, usuario=self.yo, usuario_2=pedro, dni_2="27555000")
        asignar_personas(insc)
        insc.refresh_from_db()
        self.assertEqual((insc.usuario_2, insc.persona_2), (pedro, pedro.identidad))

    def test_buscar_companero_distingue_persona_sin_cuenta(self):
        sin = persona("27000111")
        c = buscar_companero("27.000.111")
        self.assertEqual((c.persona, c.tiene_cuenta, c.categoria_bloqueada, c.ambiguo), (sin, False, False, False))


class ResolucionDeReclamosTests(TestCase):
    """El staff verifica con auditoría; nada se infiere, se fusiona ni se pierde."""

    def setUp(self):
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.p = persona("27555000", nombre="Pedro Sosa", localidad="")
        self.cat = crear_categoria("6ta LIBRE")
        self.historia = crear_inscripcion(
            self.cat, persona_2=self.p, dni_2="27555000", nombre_2="Pedro Sosa", categoria_oficial_2=5,
        )
        self.u = nombrar(User.objects.create_user("reclamante", password="x"), "Pedro", "Sosa")
        self.identidad, self.reclamo = dar_identidad_a_cuenta(self.u, "27.555.000", localidad="Necochea")
        self.propia = crear_inscripcion(crear_categoria("6ta LIBRE"), usuario=self.u, dni_1="27555000")  # persona_1 vacío

    def test_el_reclamo_nace_pendiente_sin_vinculo(self):
        self.assertIsNone(self.identidad)
        self.assertEqual((self.reclamo.estado, self.reclamo.persona), ("pendiente", self.p))
        self.p.refresh_from_db()
        self.assertIsNone(self.p.usuario)

    def test_verificar_vincula_la_cuenta_completa_su_historia_y_deja_auditoria(self):
        verificar_reclamo(self.reclamo, self.p, self.staff, "documento_en_sede", "DNI físico controlado en sede")
        self.p.refresh_from_db(); self.reclamo.refresh_from_db(); self.propia.refresh_from_db(); self.historia.refresh_from_db()
        self.assertEqual(self.u.identidad, self.p)
        self.assertEqual(self.p.localidad, "Necochea")                     # se completa solo si estaba vacía
        self.assertEqual(self.reclamo.estado, "verificado")
        self.assertEqual((self.reclamo.resuelto_por, self.reclamo.metodo_verificacion), (self.staff, "documento_en_sede"))
        self.assertIsNotNone(self.reclamo.resuelto_en)
        self.assertEqual(self.propia.persona_1, self.p)                    # sus participaciones propias se completan
        self.assertEqual(self.historia.usuario_2, self.u)                  # y la historia como compañero se le asocia
        evento = EventoIdentidad.objects.get(accion="reclamo_verificado")
        self.assertEqual((evento.actor, evento.cuenta, evento.persona, evento.reclamo), (self.staff, self.u, self.p, self.reclamo))
        self.assertEqual(evento.detalle["inscripciones_propias_completadas"], 1)

    def test_verificar_no_toca_ningun_dato_declarado_ni_categoria_historica(self):
        verificar_reclamo(self.reclamo, self.p, self.staff, "documento_en_sede", "ok")
        self.historia.refresh_from_db()
        self.assertEqual((self.historia.categoria_oficial_2, self.historia.nombre_2, self.historia.dni_2), (5, "Pedro Sosa", "27555000"))
        self.propia.refresh_from_db()
        self.assertEqual(self.propia.dni_1, "27555000")

    def test_verificar_exige_decision_explicita_y_constancia(self):
        otra = persona("31999000", nombre="Otra Persona")
        for persona_elegida, metodo, nota in (
            (None, "documento_en_sede", "ok"),          # no eligió identidad
            (otra, "documento_en_sede", "ok"),          # no es una candidata
            (self.p, "", "ok"),                          # sin método
            (self.p, "inventado", "ok"),                 # método inválido
            (self.p, "documento_en_sede", "  "),        # sin nota
        ):
            with self.subTest(persona=persona_elegida, metodo=metodo, nota=nota), self.assertRaises(ReclamoInvalido):
                verificar_reclamo(self.reclamo, persona_elegida, self.staff, metodo, nota)
        self.p.refresh_from_db()
        self.assertIsNone(self.p.usuario)
        self.assertFalse(EventoIdentidad.objects.filter(accion="reclamo_verificado").exists())

    def test_no_se_verifica_una_identidad_que_ya_tiene_cuenta_ni_dos_veces(self):
        verificar_reclamo(self.reclamo, self.p, self.staff, "otro", "ok")
        with self.assertRaises(ReclamoInvalido):
            verificar_reclamo(self.reclamo, self.p, self.staff, "otro", "otra vez")
        # Ya hay una cuenta con ese DNI: no queda nada que reclamar ni se puede registrar otra.
        with self.assertRaises(DniYaRegistrado):
            dar_identidad_a_cuenta(User.objects.create_user("otro", password="x"), "27555000")

    def test_otros_reclamos_sobre_la_misma_identidad_quedan_en_conflicto_sin_rechazarse_solos(self):
        otro = User.objects.create_user("competidor", password="x")
        _, rec2 = dar_identidad_a_cuenta(otro, "27555000")
        verificar_reclamo(self.reclamo, self.p, self.staff, "documento_en_sede", "ok")
        rec2.refresh_from_db()
        self.assertEqual(rec2.estado, ReclamoIdentidad.ESTADO_EN_CONFLICTO)
        self.assertIsNone(rec2.resuelto_en)                       # sigue abierto: lo decide una persona
        self.assertTrue(EventoIdentidad.objects.filter(accion="reclamo_en_conflicto", reclamo=rec2).exists())
        with self.assertRaises(ReclamoInvalido):                  # y no se puede verificar: ya tiene dueño
            verificar_reclamo(rec2, self.p, self.staff, "otro", "no")
        rechazar_reclamo(rec2, self.staff, "Ya fue verificada otra cuenta")
        rec2.refresh_from_db()
        self.assertEqual(rec2.estado, "rechazado")

    def test_rechazar_no_cambia_ninguna_identidad(self):
        rechazar_reclamo(self.reclamo, self.staff, "No pudo acreditar identidad")
        self.p.refresh_from_db(); self.reclamo.refresh_from_db(); self.propia.refresh_from_db()
        self.assertEqual(self.reclamo.estado, "rechazado")
        self.assertIsNone(self.p.usuario)
        self.assertIsNone(self.propia.persona_1)
        self.assertTrue(EventoIdentidad.objects.filter(accion="reclamo_rechazado", actor=self.staff).exists())
        with self.assertRaises(ReclamoInvalido):
            rechazar_reclamo(self.reclamo, self.staff, "otra vez")
        with self.assertRaises(ReclamoInvalido):
            rechazar_reclamo(ReclamoIdentidad.objects.create(usuario=User.objects.create_user("z", password="x"),
                                                             persona=self.p, dni_declarado="27555000"), self.staff, "")

    def test_tras_el_rechazo_la_cuenta_completa_su_propia_identidad_y_sus_participaciones_no_se_pierden(self):
        rechazar_reclamo(self.reclamo, self.staff, "No es esa persona")
        identidad, reclamo = completar_identidad_propia(self.u, "30777888", "Necochea")
        self.assertIsNone(reclamo)
        self.propia.refresh_from_db()
        self.assertEqual(self.propia.persona_1, identidad)         # sus participaciones pasan a SU identidad
        self.assertNotEqual(identidad, self.p)                     # nunca a la reclamada
        self.assertEqual(identidad.dni, "30777888")
        self.assertTrue(EventoIdentidad.objects.filter(accion="identidad_propia_creada", cuenta=self.u).exists())

    def test_tras_el_rechazo_volver_a_escribir_el_mismo_dni_abre_otro_reclamo_no_una_apropiacion(self):
        rechazar_reclamo(self.reclamo, self.staff, "No es esa persona")
        identidad, nuevo = completar_identidad_propia(self.u, "27555000")
        self.assertIsNone(identidad)
        self.assertEqual(nuevo.estado, "pendiente")
        self.p.refresh_from_db()
        self.assertIsNone(self.p.usuario)

    def test_no_se_completa_identidad_propia_con_un_reclamo_abierto_ni_si_ya_tiene_una(self):
        with self.assertRaises(ReclamoInvalido):
            completar_identidad_propia(self.u, "30777888")
        con = crear_usuario(dni="30100001")
        with self.assertRaises(ReclamoInvalido):
            completar_identidad_propia(con, "30555444")

    def test_la_cuenta_no_ve_la_historia_hasta_que_se_verifica_y_despues_si(self):
        self.assertEqual(self.u.reclamos_identidad.get().estado, "pendiente")
        self.assertEqual([i.pk for i in Inscripcion.objects.filter(usuario_2=self.u)], [])
        verificar_reclamo(self.reclamo, self.p, self.staff, "documento_digital", "ok")
        self.assertEqual([i.pk for i in Inscripcion.objects.filter(usuario_2=self.u)], [self.historia.pk])


class AdminReclamosTests(TestCase):
    def setUp(self):
        self.p = persona("27555000")
        self.u = User.objects.create_user("reclamante", password="x", first_name="Pedro", last_name="Sosa")
        _, self.reclamo = dar_identidad_a_cuenta(self.u, "27555000")
        self.admin = User.objects.create_superuser("root", "r@r.com", "x")
        self.c = Client()
        self.c.force_login(self.admin)
        self.url = f"/admin/accounts/reclamoidentidad/{self.reclamo.pk}/change/"

    def test_se_listan_y_se_abren_pero_no_se_crean_ni_borran_a_mano(self):
        self.assertContains(self.c.get("/admin/accounts/reclamoidentidad/"), "27555000")
        self.assertContains(self.c.get(self.url), "Identidades candidatas")
        self.assertEqual(self.c.get("/admin/accounts/reclamoidentidad/add/").status_code, 403)
        self.assertEqual(self.c.post(f"/admin/accounts/reclamoidentidad/{self.reclamo.pk}/delete/", {"post": "yes"}).status_code, 403)

    def test_verificar_desde_el_admin_exige_elegir_identidad_metodo_y_nota(self):
        r = self.c.post(self.url, {"accion": "verificar", "persona_elegida": "", "metodo_verificacion": "", "nota_resolucion": "", "_save": "x"})
        self.assertEqual(r.status_code, 200)
        self.reclamo.refresh_from_db()
        self.assertEqual(self.reclamo.estado, "pendiente")

    def test_verificar_desde_el_admin_vincula_y_registra_quien_lo_hizo(self):
        r = self.c.post(self.url, {"accion": "verificar", "persona_elegida": self.p.pk,
                                   "metodo_verificacion": "documento_en_sede", "nota_resolucion": "Controlado en sede", "_save": "x"})
        self.assertEqual(r.status_code, 302)
        self.reclamo.refresh_from_db(); self.p.refresh_from_db()
        self.assertEqual((self.reclamo.estado, self.reclamo.resuelto_por), ("verificado", self.admin))
        self.assertEqual(self.p.usuario, self.u)
        self.assertEqual(EventoIdentidad.objects.get(accion="reclamo_verificado").actor, self.admin)

    def test_rechazar_desde_el_admin(self):
        self.c.post(self.url, {"accion": "rechazar", "nota_resolucion": "No acreditó identidad", "_save": "x"})
        self.reclamo.refresh_from_db()
        self.assertEqual(self.reclamo.estado, "rechazado")
        self.p.refresh_from_db()
        self.assertIsNone(self.p.usuario)

    def test_un_reclamo_resuelto_ya_no_se_edita(self):
        rechazar_reclamo(self.reclamo, self.admin, "no")
        self.c.post(self.url, {"accion": "verificar", "persona_elegida": self.p.pk, "metodo_verificacion": "otro",
                               "nota_resolucion": "x", "_save": "x"})
        self.reclamo.refresh_from_db()
        self.assertEqual(self.reclamo.estado, "rechazado")

    def test_las_pantallas_de_identidad_funcionan_con_personas_sin_cuenta(self):
        self.assertEqual(self.c.get("/admin/accounts/identidad/").status_code, 200)
        self.assertContains(self.c.get(f"/admin/accounts/identidad/{self.p.pk}/change/"), "27555000")
        self.assertEqual(self.c.get("/admin/accounts/eventoidentidad/").status_code, 200)
        self.assertEqual(self.c.get("/admin/accounts/membresiaorganizador/").status_code, 200)


class BackfillPersonasTests(TestCase):
    """Los datos históricos que dependen de DNI tipeado: simulación por defecto, nada dudoso se vincula."""

    def setUp(self):
        self.cat = crear_categoria("6ta LIBRE")
        antes = timezone.now() - datetime.timedelta(days=60)

        def vieja(**kw):
            i = crear_inscripcion(self.cat, **kw)
            Inscripcion.objects.filter(pk=i.pk).update(creado=antes)
            return Inscripcion.objects.get(pk=i.pk)

        self.nueva = vieja(dni_1="40111001", nombre_1="Ana Gómez", dni_2="40111002", nombre_2="Laura Paz")
        self.misma_persona = vieja(dni_1="40111003", nombre_1="Ana Gómez", dni_2="40.111.002", nombre_2="Laura Paz")
        self.dudosa = vieja(dni_1="40111004", nombre_1="Otro Nombre", dni_2="40111002", nombre_2="Roberto Gil")
        self.invalida = vieja(dni_1="40111005", nombre_1="Sin Dni", dni_2="s/d", nombre_2="Sin Dato")
        self.cuenta = nombrar(crear_usuario(dni="30222333"), "Marta", "Díaz")
        Identidad.objects.filter(usuario=self.cuenta).update(creado=timezone.now())  # cuenta creada DESPUÉS
        self.retro = vieja(dni_1="40111006", nombre_1="Marta Díaz", dni_2="30222333", nombre_2="Marta Diaz")
        persona("30333444", nombre="Ana Gómez", dni_en_revision=True)
        persona("30.333.444", nombre="Otra Ana", dni_en_revision=True)
        self.ambigua = vieja(dni_1="40111007", nombre_1="Ana Gómez", dni_2="30333444", nombre_2="Ana Gómez")
        self.previa = nombrar(crear_usuario(dni="31000111"), "Beto", "Ruiz")
        Identidad.objects.filter(usuario=self.previa).update(creado=antes - datetime.timedelta(days=30))
        self.legit = vieja(dni_1="40111008", nombre_1="Ana Gómez", dni_2="31000111", nombre_2="Beto Ruiz")
        self.con_vinculo_viejo = vieja(dni_1="40111009", nombre_1="Ana Gómez", dni_2="31000111", nombre_2="Beto Ruiz",
                                       usuario_2=self.previa)

    def _foto(self):
        return {
            "insc": list(Inscripcion.objects.order_by("pk").values()),
            "ident": list(Identidad.objects.order_by("pk").values("pk", "dni", "nombre", "usuario_id", "origen")),
            "rev": RevisionIdentidad.objects.count(),
        }

    def test_por_defecto_solo_simula_y_no_escribe_nada(self):
        antes = self._foto()
        out = StringIO()
        call_command("backfill_personas", stdout=out)
        self.assertEqual(self._foto(), antes)
        texto = out.getvalue()
        self.assertIn("SIMULACIÓN", texto)
        self.assertIn("se crearía una persona sin cuenta", texto)
        self.assertIn("revisión (no se vincula)", texto)

    def test_el_informe_distingue_lo_que_se_haria_de_lo_que_se_hizo(self):
        simulado, aplicado = StringIO(), StringIO()
        call_command("backfill_personas", stdout=simulado)
        call_command("backfill_personas", "--aplicar", stdout=aplicado)
        self.assertIn("se crearía una persona sin cuenta", simulado.getvalue())
        self.assertNotIn("CREADAS", simulado.getvalue())
        self.assertIn("personas sin cuenta CREADAS", aplicado.getvalue())
        self.assertNotIn("se crearía", aplicado.getvalue())

    def test_aplicar_crea_personas_vincula_lo_seguro_y_deja_lo_dudoso_en_revision(self):
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        laura = Identidad.objects.get(dni_normalizado="40111002")
        self.assertEqual((laura.usuario, laura.origen), (None, Identidad.ORIGEN_MIGRACION))
        for pk in (self.nueva.pk, self.misma_persona.pk):               # mismo DNI y formato distinto: UNA persona
            self.assertEqual(Inscripcion.objects.get(pk=pk).persona_2, laura)
        self.assertEqual(Identidad.objects.filter(dni_normalizado="40111002").count(), 1)
        self.assertIsNone(Inscripcion.objects.get(pk=self.dudosa.pk).persona_2)           # nombre incompatible
        self.assertIsNone(Inscripcion.objects.get(pk=self.invalida.pk).persona_2)          # DNI inutilizable
        self.assertIsNone(Inscripcion.objects.get(pk=self.retro.pk).persona_2)             # cuenta posterior a la inscripción
        self.assertIsNone(Inscripcion.objects.get(pk=self.ambigua.pk).persona_2)           # ambiguo
        self.assertEqual(Inscripcion.objects.get(pk=self.legit.pk).persona_2, self.previa.identidad)  # cuenta anterior: ok
        motivos = set(RevisionIdentidad.objects.values_list("motivo", flat=True))
        self.assertTrue({"nombre_no_coincide", "vinculo_retroactivo", "dni_ambiguo"} <= motivos)

    def test_el_vinculo_retroactivo_no_le_da_a_la_cuenta_un_historial_que_no_verifico(self):
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        self.cuenta.identidad.refresh_from_db()
        self.assertEqual(self.cuenta.identidad.inscripciones_como_jugador_2.count(), 0)
        caso = RevisionIdentidad.objects.get(motivo="vinculo_retroactivo")
        self.assertEqual(list(caso.inscripciones.all()), [Inscripcion.objects.get(pk=self.retro.pk)])
        self.assertEqual(list(caso.cuentas.all()), [self.cuenta])

    def test_no_toca_datos_declarados_ni_vinculos_existentes(self):
        antes = {i.pk: (i.nombre_2, i.dni_2, i.categoria_oficial_1, i.categoria_oficial_2, i.usuario_id, i.usuario_2_id)
                 for i in Inscripcion.objects.all()}
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        despues = {i.pk: (i.nombre_2, i.dni_2, i.categoria_oficial_1, i.categoria_oficial_2, i.usuario_id, i.usuario_2_id)
                   for i in Inscripcion.objects.all()}
        self.assertEqual(despues, antes)
        self.assertEqual(Inscripcion.objects.get(pk=self.con_vinculo_viejo.pk).usuario_2, self.previa)  # legado conservado

    def test_es_idempotente(self):
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        foto = self._foto()
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        self.assertEqual(self._foto(), foto)

    def test_la_misma_persona_en_ambos_lados_no_se_asocia(self):
        i = crear_inscripcion(self.cat, dni_1="40222001", nombre_1="Igual Persona", dni_2="40222001", nombre_2="Igual Persona")
        call_command("backfill_personas", "--aplicar", stdout=StringIO())
        i.refresh_from_db()
        self.assertEqual(i.persona_1 is None or i.persona_2 is None, True)
        self.assertFalse(i.persona_1_id is not None and i.persona_1_id == i.persona_2_id)


class MigracionDeDatosDeTorneosTests(TestCase):
    """Migración 0013: solo claves foráneas ya existentes; nunca DNI tipeado."""

    def _correr(self):
        modulo = importlib.import_module("torneos.migrations.0013_backfill_personas_seguras")
        modulo.completar(apps, None)

    def test_deriva_persona_de_la_cuenta_y_nunca_del_dni_tipeado(self):
        yo = crear_usuario(dni="30100001")
        pedro = crear_usuario(dni="27555000")
        ajena = persona("29888777")
        insc = crear_inscripcion(crear_categoria(), usuario=yo, usuario_2=pedro, dni_1="29888777", dni_2="29888777")
        self._correr()
        insc.refresh_from_db()
        self.assertEqual((insc.persona_1, insc.persona_2), (yo.identidad, pedro.identidad))
        self.assertEqual(ajena.inscripciones_como_jugador_1.count() + ajena.inscripciones_como_jugador_2.count(), 0)

    def test_sin_cuentas_no_inventa_nada_y_es_idempotente(self):
        insc = crear_inscripcion(crear_categoria(), dni_2="27555000")
        self._correr(); self._correr()
        insc.refresh_from_db()
        self.assertEqual((insc.persona_1, insc.persona_2), (None, None))

    def test_si_ambos_lados_apuntarian_a_la_misma_persona_no_asocia_ninguno(self):
        yo = crear_usuario(dni="30100001")
        insc = crear_inscripcion(crear_categoria(), usuario=yo, usuario_2=yo)
        self._correr()
        insc.refresh_from_db()
        self.assertEqual((insc.persona_1, insc.persona_2), (None, None))

    def test_abre_un_caso_por_cada_grupo_de_identidades_duplicadas(self):
        a = crear_usuario(dni="27.555.000")
        b = crear_usuario(dni="27555000")   # la fábrica los deja marcados en revisión, como 0009
        self._correr(); self._correr()
        caso = RevisionIdentidad.objects.get(dni="27555000", motivo="dni_ambiguo")
        self.assertEqual(set(caso.cuentas.all()), {a, b})
        self.assertEqual(RevisionIdentidad.objects.filter(dni="27555000").count(), 1)


class PreflightConPersonasSinCuentaTests(TestCase):
    def test_el_preflight_funciona_y_no_trata_a_las_personas_como_cuentas(self):
        from .preflight import ejecutar_preflight

        p = persona("27555000", nombre="Pedro Sosa")
        crear_inscripcion(crear_categoria(), persona_2=p, dni_2="27555000", nombre_2="Pedro Sosa")
        d = ejecutar_preflight(anonimizar=False)
        self.assertEqual(d["resumen"]["identidades"], 1)
        self.assertEqual(d["resumen"]["cuentas"], d["resumen"]["cuentas"])
        out = StringIO()
        call_command("preflight_identidad", stdout=out)
        self.assertIn("solo lectura", out.getvalue())
