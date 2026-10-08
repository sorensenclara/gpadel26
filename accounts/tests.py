from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from accounts.identidad import dar_identidad_a_cuenta
from accounts.models import (
    EventoIdentidad,
    Identidad,
    MembresiaOrganizador,
    Organizador,
    PermisoMembresia,
    ReclamoIdentidad,
    dni_ya_registrado,
    nivel_de_permiso,
    tiene_permiso,
)
from torneos.models import Inscripcion
from torneos.testing import crear_categoria, crear_inscripcion, crear_usuario

User = get_user_model()


def persona_sin_cuenta(dni="27555000", nombre="Pedro Sosa", **extra):
    return Identidad.objects.create(usuario=None, dni=dni, nombre=nombre, origen=Identidad.ORIGEN_INSCRIPCION, **extra)


def datos_registro(**extra):
    datos = {
        "nombre": "Pedro", "apellido": "Sosa", "dni": "27555000", "localidad": "Necochea", "rol": "jugador",
        "celular": "1", "username": "pedro_nuevo", "password1": "clave12345", "password2": "clave12345",
    }
    datos.update(extra)
    return datos


class IdentidadPersonaTests(TestCase):
    def test_normaliza_el_dni_al_guardar_y_al_actualizar(self):
        i = persona_sin_cuenta(dni="27.555.000")
        self.assertEqual(i.dni_normalizado, "27555000")
        i.dni = "30 100 001"
        i.save(update_fields=["dni"])
        i.refresh_from_db()
        self.assertEqual(i.dni_normalizado, "30100001")

    def test_una_persona_sin_cuenta_exige_nombre(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Identidad.objects.create(usuario=None, dni="27555000", nombre="")

    def test_el_dni_equivalente_no_se_puede_duplicar(self):
        persona_sin_cuenta("27.555.000")
        with self.assertRaises(IntegrityError), transaction.atomic():
            persona_sin_cuenta("27555000", nombre="Otro")

    def test_el_dni_equivalente_solo_se_admite_si_ambas_estan_en_revision(self):
        """Así quedan los datos históricos ambiguos: marcados, nunca fusionados."""
        a = persona_sin_cuenta("27.555.000")
        a.dni_en_revision = True
        a.save()
        b = persona_sin_cuenta("27555000", nombre="Otro", dni_en_revision=True)
        self.assertEqual(Identidad.objects.filter(dni_normalizado="27555000").count(), 2)
        self.assertTrue(a.dni_en_revision and b.dni_en_revision)

    def test_borrar_la_cuenta_conserva_la_identidad_y_su_historial(self):
        yo = crear_usuario(dni="30100001", categoria_oficial=6)
        insc = crear_inscripcion(crear_categoria(), usuario=yo, persona_1=yo.identidad)
        identidad_pk = yo.identidad.pk
        yo.delete()
        persona = Identidad.objects.get(pk=identidad_pk)
        self.assertIsNone(persona.usuario)
        insc.refresh_from_db()
        self.assertEqual(insc.persona_1_id, identidad_pk)

    def test_no_se_borra_una_persona_con_participaciones(self):
        from django.db.models import ProtectedError

        persona = persona_sin_cuenta()
        crear_inscripcion(crear_categoria(), persona_2=persona)
        with self.assertRaises(ProtectedError):
            persona.delete()

    def test_dni_ya_registrado_solo_cuenta_si_hay_cuenta(self):
        persona_sin_cuenta("27555000")
        crear_usuario(dni="30100001")
        self.assertFalse(dni_ya_registrado("27.555.000"))   # persona sin cuenta: se puede reclamar, no se rechaza
        self.assertTrue(dni_ya_registrado("30.100.001"))    # una cuenta ya lo tiene
        self.assertFalse(dni_ya_registrado("30100001", excluir_pk=Identidad.objects.get(dni="30100001").pk))

    def test_nombre_completo(self):
        self.assertEqual(persona_sin_cuenta(nombre="Sosa Pedro").nombre_completo, "Sosa Pedro")
        u = crear_usuario(dni="30100001")
        u.first_name, u.last_name = "Ana", "Gómez"
        u.save()
        self.assertEqual(Identidad.objects.get(usuario=u).nombre_completo, "Ana Gómez")
        self.assertIn("DNI 31222333", str(persona_sin_cuenta("31222333")))


class RegistroConReclamoTests(TestCase):
    def test_dni_libre_crea_la_identidad_propia_sin_reclamo(self):
        r = Client().post("/registro/", datos_registro())
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="pedro_nuevo")
        self.assertEqual(u.identidad.dni, "27555000")
        self.assertEqual(u.identidad.origen, Identidad.ORIGEN_CUENTA)
        self.assertFalse(ReclamoIdentidad.objects.exists())
        self.assertTrue(EventoIdentidad.objects.filter(accion="cuenta_con_identidad", cuenta=u).exists())

    def test_dni_de_otra_cuenta_se_rechaza(self):
        crear_usuario(dni="27555000")
        r = Client().post("/registro/", datos_registro(dni="27.555.000"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="pedro_nuevo").exists())

    def test_dni_de_una_persona_sin_cuenta_NO_se_apropia_de_ella_ni_de_su_historial(self):
        persona = persona_sin_cuenta("27555000", nombre="Pedro Sosa")
        previa = crear_inscripcion(crear_categoria(), persona_2=persona, dni_2="27555000", nombre_2="Pedro Sosa")
        r = Client().post("/registro/", datos_registro(dni="27.555.000"), follow=True)
        self.assertEqual(r.status_code, 200)

        u = User.objects.get(username="pedro_nuevo")
        persona.refresh_from_db()
        self.assertIsNone(persona.usuario)                       # la identidad sigue sin cuenta
        self.assertFalse(Identidad.objects.filter(usuario=u).exists())  # y la cuenta no tiene identidad (aún)
        previa.refresh_from_db()
        self.assertIsNone(previa.usuario_2)                      # nada del historial se le atribuye

        reclamo = u.reclamos_identidad.get()
        self.assertEqual(reclamo.estado, ReclamoIdentidad.ESTADO_PENDIENTE)
        self.assertEqual(reclamo.persona, persona)
        self.assertEqual(list(reclamo.candidatas.all()), [persona])
        self.assertEqual(reclamo.dni_normalizado, "27555000")
        self.assertTrue(reclamo.indicios["dni_coincide"])
        self.assertTrue(reclamo.indicios["nombre_compatible"])
        self.assertEqual(reclamo.indicios["participaciones_de_la_identidad"], 1)
        self.assertTrue(EventoIdentidad.objects.filter(accion="reclamo_creado", reclamo=reclamo).exists())
        self.assertContains(r, "verificaremos que sos esa persona")

    def test_coincidir_en_nombre_y_dni_tampoco_verifica(self):
        persona_sin_cuenta("27555000", nombre="Pedro Sosa")
        Client().post("/registro/", datos_registro())
        reclamo = ReclamoIdentidad.objects.get()
        self.assertTrue(reclamo.indicios["nombre_compatible"])  # es un indicio...
        self.assertEqual(reclamo.estado, "pendiente")           # ...nunca una verificación

    def test_con_varias_identidades_el_reclamo_queda_sin_persona_elegida(self):
        a = persona_sin_cuenta("27.555.000", dni_en_revision=True)
        b = persona_sin_cuenta("27555000", nombre="Otro Pedro", dni_en_revision=True)
        Client().post("/registro/", datos_registro())
        reclamo = ReclamoIdentidad.objects.get()
        self.assertIsNone(reclamo.persona)
        self.assertEqual(set(reclamo.candidatas.all()), {a, b})
        self.assertEqual(reclamo.indicios["cantidad_candidatas"], 2)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertTrue(a.usuario_id is None and b.usuario_id is None)  # no se eligió ni se fusionó nada

    def test_dos_cuentas_pueden_reclamar_la_misma_identidad_sin_pisarse(self):
        persona_sin_cuenta("27555000")
        Client().post("/registro/", datos_registro())
        Client().post("/registro/", datos_registro(username="otro_reclamante"))
        self.assertEqual(ReclamoIdentidad.objects.filter(estado="pendiente").count(), 2)
        self.assertIsNone(Identidad.objects.get(dni="27555000").usuario)

    def test_una_cuenta_tiene_a_lo_sumo_un_reclamo_abierto(self):
        persona = persona_sin_cuenta()
        u = User.objects.create_user("x", password="x")
        ReclamoIdentidad.objects.create(usuario=u, persona=persona, dni_declarado="27555000")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReclamoIdentidad.objects.create(usuario=u, persona=persona, dni_declarado="27555000")

    def test_servicio_dar_identidad_a_cuenta_rechaza_un_dni_de_cuenta(self):
        from accounts.identidad import DniYaRegistrado

        crear_usuario(dni="27555000")
        with self.assertRaises(DniYaRegistrado):
            dar_identidad_a_cuenta(User.objects.create_user("n", password="x"), "27.555.000")


class CuentaConReclamoPendienteTests(TestCase):
    """Una cuenta con reclamo pendiente usa el sistema, sin acceder al historial reclamado."""

    def setUp(self):
        self.persona = persona_sin_cuenta("27555000", nombre="Pedro Sosa")
        self.historia = crear_inscripcion(
            crear_categoria("6ta LIBRE"), persona_2=self.persona, dni_2="27555000", nombre_2="Pedro Sosa",
            categoria_oficial_2=5,
        )
        Client().post("/registro/", datos_registro())
        self.u = User.objects.get(username="pedro_nuevo")
        self.c = Client()
        self.c.login(username="pedro_nuevo", password="clave12345")

    def test_puede_usar_las_pantallas_principales(self):
        for url in ("/", "/torneos/", "/reservar-cancha/", "/mis-inscripciones/", "/cuenta/mi-cuenta/", "/jugadores/"):
            self.assertEqual(self.c.get(url, follow=True).status_code, 200, url)

    def test_mi_cuenta_avisa_que_esta_en_verificacion_y_no_muestra_historial(self):
        pagina = self.c.get("/cuenta/mi-cuenta/")
        self.assertContains(pagina, "Identidad en verificación")
        self.assertContains(pagina, "27555000")                        # lo que ella misma declaró
        self.assertIsNone(pagina.context["identidad"])
        self.assertEqual(pagina.context["declaradas_por_terceros"], [])
        self.assertIsNone(pagina.context["categoria_piso"])            # la categoría 5ta de la identidad no se le revela

    def test_su_participacion_nueva_queda_anclada_a_la_cuenta_y_no_a_la_identidad_reclamada(self):
        cat = crear_categoria("6ta LIBRE")
        datos = {
            "categoria": cat.id, "nombre_1": "Pedro", "apellido_1": "Sosa", "dni_1": "27555000", "celular_1": "1", "localidad_1": "Necochea",
            "categoria_oficial_1": 6, "nombre_2": "Compañero", "apellido_2": "Nuevo", "dni_2": "29111222", "localidad_2": "Tandil",
            "categoria_oficial_2": 6, "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": "",
        }
        r = self.c.post(f"/torneos/{cat.torneo.codigo}/", datos)
        self.assertEqual(r.status_code, 302)
        nueva = Inscripcion.objects.get(usuario=self.u)
        self.assertEqual(nueva.dni_1, "27555000")          # el DNI declarado queda como se tipeó
        self.assertIsNone(nueva.persona_1)                 # pero NO se le asigna la identidad reclamada
        self.assertIsNotNone(nueva.persona_2)              # su compañero sí queda identificado
        self.assertEqual(self.persona.inscripciones_como_jugador_1.count(), 0)
        self.assertEqual(self.persona.inscripciones_como_jugador_2.count(), 1)  # solo la historia original
        self.assertEqual(
            [i.pk for i in self.c.get("/mis-inscripciones/").context["inscripciones"]], [nueva.pk]
        )  # la cuenta ve lo suyo, no la historia reclamada

    def test_un_tercero_que_nombra_ese_dni_lo_asocia_a_la_identidad_y_no_a_la_cuenta_reclamante(self):
        otro = crear_usuario(dni="30100001", categoria_oficial=6)
        cat = crear_categoria("6ta LIBRE")
        c = Client()
        c.login(username=otro.username, password="x")
        c.post(f"/torneos/{cat.torneo.codigo}/", {
            "categoria": cat.id, "nombre_1": "Otro", "apellido_1": "Prueba", "dni_1": "30100001", "celular_1": "1", "localidad_1": "Tandil",
            "categoria_oficial_1": 6, "nombre_2": "Pedro", "apellido_2": "Sosa", "dni_2": "27555000", "localidad_2": "Necochea",
            "categoria_oficial_2": 6, "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": ""})
        nueva = Inscripcion.objects.get(usuario=otro)
        self.assertEqual(nueva.persona_2, self.persona)
        self.assertIsNone(nueva.usuario_2)                 # la cuenta reclamante NO figura vinculada

    def test_las_categorias_historicas_de_la_identidad_no_cambian(self):
        self.historia.refresh_from_db()
        self.assertEqual(self.historia.categoria_oficial_2, 5)
        self.assertEqual(self.historia.nombre_2, "Pedro Sosa")


class MembresiasYPermisosTests(TestCase):
    def setUp(self):
        self.fundador = User.objects.create_user("fundador", password="x")
        self.org = Organizador.objects.create(usuario=self.fundador, rol="liga", nombre_liga="Liga Necochea", celular="1")
        self.otra = User.objects.create_user("otra", password="x")

    def test_un_organizador_nuevo_nace_con_su_membresia_y_permisos_explicitos_en_todas_las_areas(self):
        m = MembresiaOrganizador.objects.get(organizador=self.org, usuario=self.fundador)
        self.assertTrue(m.activa)
        self.assertEqual(
            {p.area: p.nivel for p in m.permisos.all()},
            {a: "administrar" for a, _ in PermisoMembresia.AREA_CHOICES},
        )

    def test_ser_miembro_no_da_ningun_permiso(self):
        MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)
        for area, _ in PermisoMembresia.AREA_CHOICES:
            self.assertIsNone(nivel_de_permiso(self.otra, self.org, area))
            self.assertFalse(tiene_permiso(self.otra, self.org, area))

    def test_cada_area_se_concede_por_separado(self):
        m = MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)
        PermisoMembresia.objects.create(membresia=m, area="rankings", nivel="administrar")
        PermisoMembresia.objects.create(membresia=m, area="canchas", nivel="usar")
        self.assertTrue(tiene_permiso(self.otra, self.org, "rankings", "administrar"))
        self.assertTrue(tiene_permiso(self.otra, self.org, "rankings", "usar"))     # administrar implica usar
        self.assertTrue(tiene_permiso(self.otra, self.org, "canchas", "usar"))
        self.assertFalse(tiene_permiso(self.otra, self.org, "canchas", "administrar"))
        self.assertFalse(tiene_permiso(self.otra, self.org, "torneos"))              # sin regla explícita: nada
        self.assertFalse(tiene_permiso(self.otra, self.org, "miembros"))

    def test_una_membresia_inactiva_no_da_nada(self):
        m = MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra, activa=False)
        PermisoMembresia.objects.create(membresia=m, area="rankings", nivel="administrar")
        self.assertFalse(tiene_permiso(self.otra, self.org, "rankings"))

    def test_los_permisos_son_de_una_entidad_y_no_de_otra(self):
        otra_entidad = Organizador.objects.create(usuario=self.otra, rol="liga", nombre_liga="Otra Liga", celular="1")
        self.assertTrue(tiene_permiso(self.otra, otra_entidad, "rankings", "administrar"))
        self.assertFalse(tiene_permiso(self.otra, self.org, "rankings"))

    def test_anonimo_y_none_no_tienen_permisos(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(tiene_permiso(AnonymousUser(), self.org, "rankings"))
        self.assertFalse(tiene_permiso(None, self.org, "rankings"))

    def test_la_entidad_no_se_queda_sin_quien_administre_sus_miembros(self):
        def fresca():
            return MembresiaOrganizador.objects.get(organizador=self.org, usuario=self.fundador)

        with self.assertRaises(ValidationError):
            fresca().delete()
        m = fresca()
        m.activa = False
        with self.assertRaises(ValidationError):
            m.save()
        with self.assertRaises(ValidationError):
            fresca().permisos.get(area="miembros").delete()
        permiso = fresca().permisos.get(area="miembros")
        permiso.nivel = "usar"
        with self.assertRaises(ValidationError):
            permiso.save()
        self.assertTrue(tiene_permiso(self.fundador, self.org, "miembros", "administrar"))  # nada cambió

    def test_con_otro_administrador_de_miembros_ya_se_puede_quitar_al_fundador(self):
        m2 = MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)
        PermisoMembresia.objects.create(membresia=m2, area="miembros", nivel="administrar")
        m = MembresiaOrganizador.objects.get(organizador=self.org, usuario=self.fundador)
        m.activa = False
        m.save()  # no lanza
        self.assertFalse(tiene_permiso(self.fundador, self.org, "rankings"))
        self.assertTrue(tiene_permiso(self.otra, self.org, "miembros", "administrar"))

    def test_una_cuenta_no_puede_estar_dos_veces_en_la_misma_entidad(self):
        MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)

    def test_el_permiso_de_membresia_no_cambia_el_poder_actual_sobre_torneos(self):
        """Compatibilidad: quién gestiona un torneo hoy (el organizador dueño) sigue igual."""
        MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.otra)
        cat = crear_categoria("6ta LIBRE")
        c = Client()
        c.login(username="otra", password="x")
        self.assertEqual(c.get(f"/organizador/torneos/{cat.torneo.codigo}/").status_code, 403)


class MigracionDeDatosDeAccountsTests(TestCase):
    """La migración 0009 solo agrega información derivada y deja marcados los duplicados."""

    def _correr(self):
        import importlib

        from django.apps import apps

        modulo = importlib.import_module("accounts.migrations.0009_backfill_identidad_y_membresias")
        modulo.completar_datos(apps, None)

    def test_calcula_el_dni_normalizado_y_marca_los_duplicados_sin_fusionar(self):
        a = crear_usuario(dni="27.555.000")
        b = crear_usuario(dni="27555000")      # la fábrica ya los deja marcados, igual que la migración
        Identidad.objects.update(dni_normalizado="", dni_en_revision=False)  # estado "previo a la migración"
        Identidad.objects.filter(pk__in=[]).update()  # (sin efecto)
        sola = crear_usuario(dni="30100001")
        Identidad.objects.update(dni_normalizado="", dni_en_revision=False)

        self._correr()

        a.identidad.refresh_from_db(); b.identidad.refresh_from_db(); sola.identidad.refresh_from_db()
        self.assertEqual((a.identidad.dni_normalizado, b.identidad.dni_normalizado), ("27555000", "27555000"))
        self.assertTrue(a.identidad.dni_en_revision and b.identidad.dni_en_revision)
        self.assertFalse(sola.identidad.dni_en_revision)
        self.assertEqual(Identidad.objects.count(), 3)               # no se borró ni se fusionó nada
        self.assertEqual((a.identidad.dni, b.identidad.dni), ("27.555.000", "27555000"))  # el DNI original, intacto

    def test_da_membresia_con_los_mismos_poderes_a_los_organizadores_existentes(self):
        MembresiaOrganizador.objects.all().delete()   # como estaba antes de la migración
        org = User.objects.create_user("viejo", password="x")
        Organizador.objects.create(usuario=org, rol="liga", nombre_liga="Vieja", celular="1")
        MembresiaOrganizador.objects.all().delete()
        self._correr()
        self.assertTrue(tiene_permiso(org, org.organizador, "torneos", "administrar"))
        self._correr()  # idempotente
        self.assertEqual(MembresiaOrganizador.objects.filter(usuario=org).count(), 1)
        self.assertEqual(PermisoMembresia.objects.filter(membresia__usuario=org).count(), 4)
