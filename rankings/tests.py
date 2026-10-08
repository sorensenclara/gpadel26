import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase

from accounts.models import Identidad, Jugador, MembresiaOrganizador, Organizador, PermisoMembresia
from torneos.testing import crear_categoria, crear_usuario

from . import permisos
from .models import PuntajeRanking, Ranking, ReglaPuntaje, TemporadaRanking, TorneoPuntuable, VersionRanking
from .servicios import (
    crear_version_borrador,
    nueva_version_desde,
    publicar_version,
    puntajes_de_pareja,
    version_vigente,
)

User = get_user_model()


def entidad(username="liga", nombre="Liga Necochea"):
    usuario = User.objects.create_user(username, password="x")
    return Organizador.objects.create(usuario=usuario, rol="liga", nombre_liga=nombre, celular="1")


def persona(dni, nombre="Jugador Prueba"):
    return Identidad.objects.create(usuario=None, dni=dni, nombre=nombre, origen=Identidad.ORIGEN_INSCRIPCION)


def ranking_externo(org, nombre="Ranking Necochea", **extra):
    return Ranking.objects.create(nombre=nombre, tipo=Ranking.TIPO_EXTERNO, entidad=org, **extra)


def temporada(ranking, nombre="2026"):
    return TemporadaRanking.objects.create(ranking=ranking, nombre=nombre)


def version_con(temporada_, filas, publicar=True, por=None):
    """filas: [(persona, puntos[, categoria[, posicion]])]. Se arma directo (sin permisos)."""
    v = VersionRanking.objects.create(temporada=temporada_)
    for fila in filas:
        p, puntos = fila[0], fila[1]
        PuntajeRanking.objects.create(
            version=v, persona=p, puntos=puntos, categoria=fila[2] if len(fila) > 2 else "",
            posicion=fila[3] if len(fila) > 3 else None,
        )
    if publicar:
        v.estado, v.publicada_en, v.publicada_por = VersionRanking.ESTADO_PUBLICADA, datetime.datetime.now(datetime.timezone.utc), por
        v.save()
    return v


class RankingModeloTests(TestCase):
    def setUp(self):
        self.org = entidad()

    def test_el_ranking_general_no_tiene_entidad_y_es_publico(self):
        Ranking.objects.create(nombre="General GPADEL", tipo=Ranking.TIPO_GENERAL, visibilidad="publico")
        for extra in ({"entidad": self.org, "visibilidad": "publico"}, {"visibilidad": "privado"}):
            with self.subTest(extra=extra), self.assertRaises(IntegrityError), transaction.atomic():
                Ranking.objects.create(nombre="Otro general", tipo=Ranking.TIPO_GENERAL, **extra)

    def test_hay_un_solo_ranking_general(self):
        Ranking.objects.create(nombre="General", tipo=Ranking.TIPO_GENERAL, visibilidad="publico")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ranking.objects.create(nombre="General 2", tipo=Ranking.TIPO_GENERAL, visibilidad="publico")

    def test_un_ranking_externo_siempre_tiene_entidad(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ranking.objects.create(nombre="Huérfano", tipo=Ranking.TIPO_EXTERNO)

    def test_el_nombre_es_unico_por_entidad_pero_se_repite_entre_entidades(self):
        ranking_externo(self.org, "Ranking 2026")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ranking_externo(self.org, "Ranking 2026")
        ranking_externo(entidad("otra", "Otra Liga"), "Ranking 2026")  # no lanza

    def test_el_ranking_existe_sin_ningun_torneo(self):
        r = ranking_externo(self.org, ambito="Necochea", visibilidad="publico", por_categoria=True)
        self.assertEqual((r.ambito, r.visibilidad, r.por_categoria), ("Necochea", "publico", True))

    def test_por_categoria_se_puede_cambiar_hasta_que_se_publica_una_version(self):
        r = ranking_externo(self.org)
        r.por_categoria = True
        r.save()
        version_con(temporada(r), [(persona("27555000"), 10, "6ta")])
        r.por_categoria = False
        with self.assertRaises(ValidationError):
            r.save()
        r.refresh_from_db()
        self.assertTrue(r.por_categoria)

    def test_no_se_borra_una_entidad_que_tiene_rankings(self):
        from django.db.models import ProtectedError

        ranking_externo(self.org)
        with self.assertRaises(ProtectedError):
            self.org.delete()


class TemporadaYVersionTests(TestCase):
    def setUp(self):
        self.org = entidad()
        self.admin = self.org.usuario  # fundador: administra rankings
        self.r = ranking_externo(self.org)
        self.t = temporada(self.r)
        self.p1, self.p2 = persona("27555000"), persona("30100001")

    def test_la_temporada_es_unica_por_ranking_y_sus_fechas_coherentes(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            temporada(self.r, "2026")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TemporadaRanking.objects.create(ranking=self.r, nombre="2027", fecha_desde=datetime.date(2027, 6, 1),
                                            fecha_hasta=datetime.date(2027, 1, 1))

    def test_las_versiones_se_numeran_solas_y_hay_un_solo_borrador(self):
        v1 = crear_version_borrador(self.t, self.admin)
        self.assertEqual((v1.numero, v1.estado), (1, "borrador"))
        with self.assertRaises(ValidationError):
            crear_version_borrador(self.t, self.admin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            VersionRanking.objects.create(temporada=self.t)   # ni siquiera salteando el servicio
        PuntajeRanking.objects.create(version=v1, persona=self.p1, puntos=10)
        publicar_version(v1, self.admin)
        self.assertEqual(crear_version_borrador(self.t, self.admin).numero, 2)

    def test_no_se_publica_una_version_vacia_y_se_registra_quien_y_cuando(self):
        v = crear_version_borrador(self.t, self.admin)
        with self.assertRaises(ValidationError):
            publicar_version(v, self.admin)
        PuntajeRanking.objects.create(version=v, persona=self.p1, puntos=10)
        publicar_version(v, self.admin)
        v.refresh_from_db()
        self.assertEqual((v.estado, v.publicada_por), ("publicada", self.admin))
        self.assertIsNotNone(v.publicada_en)
        with self.assertRaises(ValidationError):
            publicar_version(v, self.admin)

    def test_una_version_publicada_es_inmutable_de_todas_las_formas(self):
        v = version_con(self.t, [(self.p1, 10, "", 1)])
        fila = v.puntajes.get()
        intentos = {
            "guardar la versión": lambda: (setattr(v, "nota", "x"), v.save()),
            "borrar la versión": lambda: v.delete(),
            "guardar un puntaje": lambda: (setattr(fila, "puntos", 99), fila.save()),
            "borrar un puntaje": lambda: fila.delete(),
            "update masivo": lambda: PuntajeRanking.objects.filter(version=v).update(puntos=99),
            "delete masivo": lambda: PuntajeRanking.objects.filter(version=v).delete(),
            "agregar un puntaje": lambda: PuntajeRanking.objects.create(version=v, persona=self.p2, puntos=5),
            "bulk_create": lambda: PuntajeRanking.objects.bulk_create([PuntajeRanking(version=v, persona=self.p2, puntos=5)]),
        }
        for nombre, intento in intentos.items():
            with self.subTest(nombre), self.assertRaises(ValidationError):
                intento()
        self.assertEqual(list(v.puntajes.values_list("persona_id", "puntos")), [(self.p1.pk, Decimal("10.00"))])
        self.assertEqual(VersionRanking.objects.get(pk=v.pk).nota, "")

    def test_corregir_un_ranking_publicado_es_publicar_una_version_nueva_sin_tocar_la_anterior(self):
        v1 = version_con(self.t, [(self.p1, 10, "", 1), (self.p2, 8, "", 2)])
        borrador = nueva_version_desde(v1, self.admin, "Corrección de puntos")
        self.assertEqual((borrador.numero, borrador.estado, borrador.nota), (2, "borrador", "Corrección de puntos"))
        self.assertEqual(borrador.puntajes.count(), 2)                       # copiada
        fila = borrador.puntajes.get(persona=self.p1)
        fila.puntos = Decimal("12")
        fila.save()
        publicar_version(borrador, self.admin)
        self.assertEqual(v1.puntajes.get(persona=self.p1).puntos, Decimal("10.00"))   # la v1 sigue intacta
        self.assertEqual(borrador.puntajes.get(persona=self.p1).puntos, Decimal("12.00"))
        self.assertEqual(version_vigente(self.t), borrador)

    def test_version_vigente_es_la_ultima_publicada_no_el_borrador(self):
        v1 = version_con(self.t, [(self.p1, 10)])
        nueva_version_desde(v1, self.admin)   # borrador v2, sin publicar
        self.assertEqual(version_vigente(self.t), v1)
        self.assertIsNone(version_vigente(temporada(self.r, "2027")))

    def test_una_temporada_cerrada_no_admite_versiones_nuevas(self):
        self.t.estado = TemporadaRanking.ESTADO_CERRADA
        self.t.save()
        with self.assertRaises(ValidationError):
            crear_version_borrador(self.t, self.admin)


class PuntajeTests(TestCase):
    def setUp(self):
        self.org = entidad()
        self.r = ranking_externo(self.org)
        self.t = temporada(self.r)
        self.v = VersionRanking.objects.create(temporada=self.t)
        self.p = persona("27555000")

    def test_una_persona_tiene_un_puntaje_por_lista(self):
        PuntajeRanking.objects.create(version=self.v, persona=self.p, puntos=10)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PuntajeRanking.objects.create(version=self.v, persona=self.p, puntos=20)

    def test_control_de_duplicados_por_dni_aunque_todavia_no_este_vinculado(self):
        PuntajeRanking.objects.create(version=self.v, persona=None, puntos=5, dni_original="30.100.001",
                                      nombre_original="Ana", estado_vinculo="pendiente")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PuntajeRanking.objects.create(version=self.v, persona=None, puntos=7, dni_original="30100001",
                                          nombre_original="Ana G", estado_vinculo="pendiente")

    def test_valores_invalidos(self):
        casos = {
            "puntos negativos": dict(puntos=-1),
            "posición cero": dict(puntos=1, posicion=0),
            "vinculado sin persona": dict(puntos=1, persona=None, estado_vinculo="vinculado", dni_original="1"),
        }
        for nombre, datos in casos.items():
            datos.setdefault("persona", self.p)
            with self.subTest(nombre), self.assertRaises(IntegrityError), transaction.atomic():
                PuntajeRanking.objects.create(version=self.v, **datos)

    def test_los_puntos_pueden_ser_decimales(self):
        PuntajeRanking.objects.create(version=self.v, persona=self.p, puntos="12.5")
        self.assertEqual(self.v.puntajes.get().puntos, Decimal("12.50"))

    def test_ranking_por_categoria_exige_lista_y_el_general_la_prohibe(self):
        general = PuntajeRanking(version=self.v, persona=self.p, puntos=1, categoria="6ta")
        with self.assertRaises(ValidationError):
            general.full_clean()
        r2 = ranking_externo(entidad("o2", "Liga 2"), por_categoria=True)
        v2 = VersionRanking.objects.create(temporada=temporada(r2))
        with self.assertRaises(ValidationError):
            PuntajeRanking(version=v2, persona=self.p, puntos=1, categoria="").full_clean()
        PuntajeRanking(version=v2, persona=self.p, puntos=1, categoria="6ta").full_clean()  # no lanza

    def test_una_persona_puede_estar_en_varias_listas_de_un_ranking_por_categoria(self):
        r2 = ranking_externo(entidad("o2", "Liga 2"), por_categoria=True)
        v2 = VersionRanking.objects.create(temporada=temporada(r2))
        PuntajeRanking.objects.create(version=v2, persona=self.p, puntos=10, categoria="6ta", posicion=1)
        PuntajeRanking.objects.create(version=v2, persona=self.p, puntos=4, categoria="Libre", posicion=7)
        self.assertEqual(v2.puntajes.filter(persona=self.p).count(), 2)

    def test_una_persona_aparece_en_varios_rankings_y_temporadas_con_puntos_y_posiciones_distintos(self):
        otra_entidad = entidad("o2", "Liga 2")
        filas = [
            (temporada(self.r, "2026b"), 100, 1), (temporada(self.r, "2027"), 40, 5),
            (temporada(ranking_externo(otra_entidad, "Ranking Tandil"), "2026"), 70, 3),
        ]
        for temp, puntos, posicion in filas:
            version_con(temp, [(self.p, puntos, "", posicion)])
        resultados = sorted((x.puntos, x.posicion) for x in PuntajeRanking.objects.filter(persona=self.p))
        self.assertEqual(resultados, [(Decimal("40.00"), 5), (Decimal("70.00"), 3), (Decimal("100.00"), 1)])
        self.assertEqual(self.p.puntajes_ranking.count(), 3)

    def test_el_historial_de_puntos_es_de_la_persona_no_de_la_pareja(self):
        """Cambiar de pareja no cambia los puntos individuales."""
        v = version_con(temporada(self.r, "otra"), [(self.p, 10)], publicar=True)
        otra = persona("30100001")
        for compañero in (otra, persona("31000111")):
            self.assertEqual(puntajes_de_pareja(v, self.p, compañero)["jugador_1"], Decimal("10.00"))


class IndependenciaDeLaCategoriaOficialTests(TestCase):
    def test_los_rankings_no_tienen_relacion_alguna_con_la_categoria_oficial(self):
        from django.apps import apps

        prohibidos = {Jugador, apps.get_model("torneos", "Categoria")}
        for modelo in apps.get_app_config("rankings").get_models():
            for campo in modelo._meta.get_fields():
                destino = getattr(campo, "related_model", None)
                self.assertNotIn(destino, prohibidos, f"{modelo.__name__}.{campo.name}")

    def test_cargar_y_publicar_puntajes_nunca_modifica_la_categoria_oficial_de_nadie(self):
        usuario = crear_usuario(dni="30100001", categoria_oficial=7)
        org = entidad()
        r = ranking_externo(org, por_categoria=True)
        # En el ranking externo es "1ra": una etiqueta propia, sin efecto sobre su categoría de GPADEL.
        version_con(temporada(r), [(usuario.identidad, 500, "1ra", 1)], por=org.usuario)
        usuario.jugador.refresh_from_db()
        self.assertEqual(usuario.jugador.categoria_oficial, 7)

    def test_las_categorias_del_ranking_son_etiquetas_libres(self):
        org = entidad()
        r = ranking_externo(org, por_categoria=True)
        v = VersionRanking.objects.create(temporada=temporada(r))
        for etiqueta in ("Damas A", "Mixto Libre", "Sub-16", "5ta"):
            PuntajeRanking(version=v, persona=persona(f"3{abs(hash(etiqueta)) % 10**7:07d}"), puntos=1, categoria=etiqueta).full_clean()


class PuntajesDeParejaTests(TestCase):
    def setUp(self):
        org = entidad()
        self.t = temporada(ranking_externo(org))
        self.a, self.b, self.c = persona("27555000"), persona("30100001"), persona("31000111")
        self.v = version_con(self.t, [(self.a, 10), (self.b, 7)])

    def test_pareja_completa_suma_los_puntos_de_ambos(self):
        r = puntajes_de_pareja(self.v, self.a, self.b)
        self.assertEqual((r["jugador_1"], r["jugador_2"], r["pareja"], r["ranking_incompleto"]),
                         (Decimal("10.00"), Decimal("7.00"), Decimal("17.00"), False))

    def test_quien_no_figura_queda_sin_ranking_y_nunca_vale_cero(self):
        r = puntajes_de_pareja(self.v, self.a, self.c)
        self.assertEqual(r["jugador_1"], Decimal("10.00"))
        self.assertIsNone(r["jugador_2"])                      # SR: ni 0 ni una posición inventada
        self.assertIsNone(r["pareja"])                         # y no se inventa una suma parcial
        self.assertTrue(r["ranking_incompleto"])

    def test_ambos_sin_ranking(self):
        r = puntajes_de_pareja(self.v, self.c, persona("32000222"))
        self.assertEqual((r["jugador_1"], r["jugador_2"], r["pareja"], r["ranking_incompleto"]), (None, None, None, True))

    def test_una_pareja_con_un_integrante_sin_identidad_tambien_es_incompleta(self):
        self.assertTrue(puntajes_de_pareja(self.v, self.a, None)["ranking_incompleto"])

    def test_nunca_se_mezclan_rankings_distintos(self):
        otra = version_con(temporada(ranking_externo(entidad("o2", "Liga 2"), "Otro Ranking")), [(self.a, 1000), (self.b, 2000)])
        self.assertEqual(puntajes_de_pareja(self.v, self.a, self.b)["pareja"], Decimal("17.00"))
        self.assertEqual(puntajes_de_pareja(otra, self.a, self.b)["pareja"], Decimal("3000.00"))

    def test_en_un_ranking_por_categoria_se_usa_la_lista_pedida(self):
        r = ranking_externo(entidad("o2", "Liga 2"), "Por categoría", por_categoria=True)
        v = version_con(temporada(r), [(self.a, 10, "6ta"), (self.b, 7, "6ta"), (self.a, 99, "Libre")])
        self.assertEqual(puntajes_de_pareja(v, self.a, self.b, "6ta")["pareja"], Decimal("17.00"))
        self.assertTrue(puntajes_de_pareja(v, self.a, self.b, "Libre")["ranking_incompleto"])


class PermisosRankingTests(TestCase):
    def setUp(self):
        self.org = entidad("fundador", "Liga Necochea")
        self.fundador = self.org.usuario
        self.admin_ent = self._miembro("admin_ent", rankings="administrar")
        self.usuario_ent = self._miembro("usuario_ent", rankings="usar")
        self.solo_torneos = self._miembro("solo_torneos", torneos="administrar")
        self.sin_permisos = self._miembro("sin_permisos")
        self.otro_org = entidad("otro_org", "Otra Liga").usuario
        self.jugador = User.objects.create_user("jugador", password="x")
        Jugador.objects.create(usuario=self.jugador, nombre="J", celular="1")
        self.staff = User.objects.create_user("staff", password="x", is_staff=True)
        self.publico = ranking_externo(self.org, "Público", visibilidad="publico")
        self.privado = ranking_externo(self.org, "Privado", visibilidad="privado")
        self.general = Ranking.objects.create(nombre="General", tipo=Ranking.TIPO_GENERAL, visibilidad="publico")

    def _miembro(self, username, **permisos_):
        u = User.objects.create_user(username, password="x")
        m = MembresiaOrganizador.objects.create(organizador=self.org, usuario=u)
        for area, nivel in permisos_.items():
            PermisoMembresia.objects.create(membresia=m, area=area, nivel=nivel)
        return u

    def _tabla(self, funcion, ranking):
        usuarios = {
            "fundador": self.fundador, "admin_ent": self.admin_ent, "usuario_ent": self.usuario_ent,
            "solo_torneos": self.solo_torneos, "sin_permisos": self.sin_permisos, "otro_org": self.otro_org,
            "jugador": self.jugador, "staff": self.staff, "anonimo": AnonymousUser(),
        }
        return {nombre for nombre, u in usuarios.items() if funcion(u, ranking)}

    def test_ver_un_ranking_publico_puede_cualquiera_sin_ver_nada_privado(self):
        self.assertEqual(len(self._tabla(permisos.puede_ver, self.publico)), 9)  # todos, incluso el anónimo
        self.assertEqual(self._tabla(permisos.puede_ver, self.privado), {"fundador", "admin_ent", "usuario_ent", "staff"})

    def test_usar_un_ranking_en_torneos(self):
        self.assertEqual(self._tabla(permisos.puede_usar, self.publico), {"fundador", "admin_ent", "usuario_ent", "solo_torneos", "otro_org"} | set())
        self.assertEqual(self._tabla(permisos.puede_usar, self.privado), {"fundador", "admin_ent", "usuario_ent"})
        # El general no tiene entidad: solo lo usan organizadores o quien tenga permiso de torneos.
        self.assertEqual(self._tabla(permisos.puede_usar, self.general), {"fundador", "solo_torneos", "otro_org"})

    def test_modificar_solo_los_administradores_autorizados_de_la_entidad_en_publicos_y_privados(self):
        esperado = {"fundador", "admin_ent"}
        self.assertEqual(self._tabla(permisos.puede_administrar, self.publico), esperado)
        self.assertEqual(self._tabla(permisos.puede_administrar, self.privado), esperado)

    def test_el_staff_no_modifica_un_ranking_externo_ni_siquiera_el_publico(self):
        self.assertFalse(permisos.puede_administrar(self.staff, self.publico))
        self.assertFalse(permisos.puede_administrar(self.staff, self.privado))

    def test_el_ranking_general_solo_lo_modifica_el_staff(self):
        self.assertEqual(self._tabla(permisos.puede_administrar, self.general), {"staff"})

    def test_ser_administrador_de_otra_area_no_da_permiso_sobre_rankings(self):
        self.assertFalse(permisos.puede_administrar(self.solo_torneos, self.publico))
        self.assertFalse(permisos.puede_ver(self.solo_torneos, self.privado))
        self.assertFalse(permisos.puede_usar(self.solo_torneos, self.privado))

    def test_ser_miembro_sin_permisos_explicitos_no_da_nada(self):
        for funcion in (permisos.puede_administrar,):
            self.assertFalse(funcion(self.sin_permisos, self.publico))
        self.assertFalse(permisos.puede_ver(self.sin_permisos, self.privado))

    def test_una_membresia_desactivada_pierde_todo(self):
        MembresiaOrganizador.objects.filter(usuario=self.admin_ent).update(activa=False)
        self.assertFalse(permisos.puede_administrar(self.admin_ent, self.publico))
        self.assertFalse(permisos.puede_usar(self.admin_ent, self.privado))

    def test_usar_en_un_torneo_exige_ademas_poder_gestionar_ese_torneo(self):
        propio = crear_categoria("6ta LIBRE").torneo
        propio.organizador = self.org
        propio.save()
        ajeno = crear_categoria("6ta LIBRE").torneo  # de otra entidad
        self.assertTrue(permisos.puede_usar_en_torneo(self.fundador, self.publico, propio))
        self.assertTrue(permisos.puede_usar_en_torneo(self.solo_torneos, self.publico, propio))     # permiso de torneos
        self.assertFalse(permisos.puede_usar_en_torneo(self.usuario_ent, self.publico, propio))     # rankings, pero no torneos
        self.assertFalse(permisos.puede_usar_en_torneo(self.otro_org, self.publico, propio))        # otra entidad
        self.assertFalse(permisos.puede_usar_en_torneo(self.fundador, self.publico, ajeno))
        self.assertFalse(permisos.puede_usar_en_torneo(self.otro_org, self.privado, ajeno))

    def test_los_servicios_exigen_permiso_de_administracion(self):
        t = temporada(self.publico)
        for usuario in (self.usuario_ent, self.solo_torneos, self.otro_org, self.staff, self.sin_permisos):
            with self.subTest(usuario=usuario.username), self.assertRaises(PermissionDenied):
                crear_version_borrador(t, usuario)
        self.assertEqual(crear_version_borrador(t, self.admin_ent).numero, 1)
        self.assertFalse(VersionRanking.objects.exclude(creada_por=self.admin_ent).exists())

    def test_publicar_y_corregir_tambien_exigen_permiso(self):
        t = temporada(self.publico)
        v = version_con(t, [(persona("27555000"), 10)])
        with self.assertRaises(PermissionDenied):
            nueva_version_desde(v, self.usuario_ent)
        borrador = crear_version_borrador(t, self.admin_ent)
        PuntajeRanking.objects.create(version=borrador, persona=persona("30100001"), puntos=3)
        with self.assertRaises(PermissionDenied):
            publicar_version(borrador, self.otro_org)

    def test_el_staff_puede_gestionar_las_versiones_del_ranking_general(self):
        v = crear_version_borrador(temporada(self.general), self.staff)
        self.assertEqual(v.creada_por, self.staff)


class TorneoPuntuableTests(TestCase):
    def setUp(self):
        self.general = Ranking.objects.create(nombre="General", tipo=Ranking.TIPO_GENERAL, visibilidad="publico")
        self.t = temporada(self.general, "2026")
        self.torneo = crear_categoria("6ta LIBRE").torneo

    def test_un_torneo_no_puntua_por_defecto(self):
        tp = TorneoPuntuable.objects.create(torneo=self.torneo, temporada=self.t)
        self.assertFalse(tp.puntuable)

    def test_para_puntuar_hace_falta_una_regla(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TorneoPuntuable.objects.create(torneo=self.torneo, temporada=self.t, puntuable=True)
        regla = ReglaPuntaje.objects.create(nombre="Regla A", parametros={"base": 100})
        tp = TorneoPuntuable.objects.create(torneo=self.torneo, temporada=self.t, puntuable=True, regla=regla)
        self.assertTrue(tp.puntuable)

    def test_solo_aporta_al_ranking_general(self):
        externo = temporada(ranking_externo(entidad(), "Externo"), "2026")
        with self.assertRaises(ValidationError):
            TorneoPuntuable.objects.create(torneo=self.torneo, temporada=externo)

    def test_un_torneo_tiene_una_sola_configuracion(self):
        TorneoPuntuable.objects.create(torneo=self.torneo, temporada=self.t)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TorneoPuntuable.objects.create(torneo=self.torneo, temporada=self.t)

    def test_la_regla_guarda_configuracion_sin_algoritmo(self):
        regla = ReglaPuntaje.objects.create(nombre="Pendiente de definir", descripcion="Sin algoritmo todavía")
        self.assertEqual((regla.parametros, regla.vigente), ({}, True))


class AdminRankingsTests(TestCase):
    def setUp(self):
        self.org = entidad("fundador", "Liga Necochea")
        self.ranking = ranking_externo(self.org, "Ranking Necochea", visibilidad="publico")
        self.v = version_con(temporada(self.ranking), [(persona("27555000"), 10)])
        self.owner = User.objects.create_superuser("dueño_admin", "a@a.com", "x")
        m = MembresiaOrganizador.objects.create(organizador=self.org, usuario=self.owner)
        PermisoMembresia.objects.create(membresia=m, area="rankings", nivel="administrar")
        self.ajeno = User.objects.create_superuser("ajeno_admin", "b@b.com", "x")
        self.url = f"/admin/rankings/ranking/{self.ranking.pk}/change/"

    def _post(self, usuario):
        c = Client()
        c.force_login(usuario)
        return c.post(self.url, {"nombre": "Renombrado", "tipo": "externo", "entidad": self.org.pk, "ambito": "",
                                 "descripcion": "", "visibilidad": "publico", "_save": "x"})

    def test_quien_no_administra_el_ranking_no_lo_puede_modificar_aunque_sea_superusuario(self):
        self.assertEqual(self._post(self.ajeno).status_code, 403)
        self.ranking.refresh_from_db()
        self.assertEqual(self.ranking.nombre, "Ranking Necochea")

    def test_quien_lo_administra_si(self):
        self.assertEqual(self._post(self.owner).status_code, 302)
        self.ranking.refresh_from_db()
        self.assertEqual(self.ranking.nombre, "Renombrado")

    def test_todos_pueden_ver_la_ficha_en_solo_lectura(self):
        c = Client()
        c.force_login(self.ajeno)
        self.assertEqual(c.get(self.url).status_code, 200)

    def test_una_version_publicada_se_muestra_en_solo_lectura(self):
        c = Client()
        c.force_login(self.owner)
        pagina = c.get(f"/admin/rankings/versionranking/{self.v.pk}/change/")
        self.assertEqual(pagina.status_code, 200)
        self.assertNotContains(pagina, 'name="puntajes-0-puntos"')   # sin campos editables
