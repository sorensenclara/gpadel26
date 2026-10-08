import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from torneos.testing import crear_categoria, crear_inscripcion, crear_usuario

from .models import (
    EventoAuditoria,
    Llave,
    Partido,
    PropuestaAsignacion,
    PropuestaFase,
    PropuestaZona,
    Resultado,
    Zona,
    ZonaPareja,
)
from .sets import resumen_sets, validar_sets

SET = lambda a, b: {"a": a, "b": b}  # noqa: E731
SUPER = lambda a, b: {"a": a, "b": b, "tipo": "super_tiebreak"}  # noqa: E731


class SetsTests(TestCase):
    def test_gana_en_dos_sets(self):
        r = resumen_sets([SET(6, 3), SET(6, 4)])
        self.assertEqual((r["ganador"], r["sets_a"], r["sets_b"], r["games_a"], r["games_b"]), ("a", 2, 0, 12, 7))

    def test_tres_sets(self):
        r = resumen_sets([SET(6, 3), SET(4, 6), SET(3, 6)])
        self.assertEqual((r["ganador"], r["sets_a"], r["sets_b"], r["cantidad_sets"]), ("b", 1, 2, 3))

    def test_super_tiebreak_no_cuenta_como_games(self):
        r = resumen_sets([SET(6, 3), SET(4, 6), SUPER(10, 7)])
        self.assertTrue(r["super_tiebreak"])
        self.assertEqual(r["ganador"], "a")
        self.assertEqual((r["games_a"], r["games_b"]), (10, 9))  # solo los 2 sets normales

    def test_formatos_invalidos(self):
        invalidos = [
            [], [SET(6, 3)], [SET(6, 3)] * 4, "6-3 6-4", [{"a": "6", "b": 3}, SET(6, 4)],
            [{"a": -1, "b": 3}, SET(6, 4)], [{"a": True, "b": 3}, SET(6, 4)],
            [SET(6, 6), SET(6, 4)],  # set empatado
        ]
        for sets in invalidos:
            with self.subTest(sets=sets), self.assertRaises(ValidationError):
                validar_sets(sets)

    def test_hace_falta_tercer_set_si_1_a_1(self):
        with self.assertRaises(ValidationError):
            validar_sets([SET(6, 3), SET(4, 6)])

    def test_sobra_tercer_set_si_ya_estaba_definido(self):
        with self.assertRaises(ValidationError):
            validar_sets([SET(6, 3), SET(6, 4), SET(6, 2)])

    def test_super_tiebreak_solo_como_tercer_set_y_con_minimos(self):
        with self.assertRaises(ValidationError):
            validar_sets([SUPER(10, 7), SET(6, 4)])
        with self.assertRaises(ValidationError):
            validar_sets([SET(6, 3), SET(4, 6), SUPER(9, 7)])   # menos de 10
        with self.assertRaises(ValidationError):
            validar_sets([SET(6, 3), SET(4, 6), SUPER(10, 9)])  # menos de 2 de diferencia
        validar_sets([SET(6, 3), SET(4, 6), SUPER(12, 10)])      # válido


def _partido_de_grupo(**extra):
    """Zona con dos parejas y un partido entre ellas."""
    cat = crear_categoria()
    zona = Zona.objects.create(categoria=cat, nombre="A")
    u1, u2, u3, u4 = (crear_usuario(), crear_usuario(), crear_usuario(), crear_usuario())
    a = crear_inscripcion(cat, usuario=u1, usuario_2=u2)
    b = crear_inscripcion(cat, usuario=u3, usuario_2=u4)
    partido = Partido.objects.create(
        categoria=cat, tipo=Partido.TIPO_GRUPO, zona=zona, pareja_a=a, pareja_b=b, **extra
    )
    return partido, (u1, u2, u3, u4)


class PartidoTests(TestCase):
    def test_nace_pendiente_y_sin_fecha(self):
        partido, _ = _partido_de_grupo()
        self.assertEqual(partido.estado, Partido.ESTADO_PENDIENTE)
        self.assertIsNone(partido.fecha_hora)

    def test_participantes_y_no_participantes(self):
        partido, (u1, u2, u3, u4) = _partido_de_grupo()
        ajeno = crear_usuario()
        self.assertEqual([partido.lado_de(u) for u in (u1, u2, u3, u4)], ["a", "a", "b", "b"])
        self.assertIsNone(partido.lado_de(ajeno))
        self.assertFalse(partido.es_participante(ajeno))
        self.assertFalse(partido.es_participante(None))

    def test_companero_sin_cuenta_no_rompe_los_participantes(self):
        cat = crear_categoria()
        zona = Zona.objects.create(categoria=cat, nombre="A")
        u1 = crear_usuario()
        a = crear_inscripcion(cat, usuario=u1)           # compañero sin cuenta
        b = crear_inscripcion(cat, usuario=crear_usuario())
        p = Partido.objects.create(categoria=cat, tipo="grupo", zona=zona, pareja_a=a, pareja_b=b)
        self.assertEqual(p.usuarios_de("a"), {u1.pk})

    def test_programado_requiere_fecha_y_pendiente_no_puede_tenerla(self):
        partido, _ = _partido_de_grupo()
        with self.assertRaises(IntegrityError), transaction.atomic():
            partido.estado = Partido.ESTADO_PROGRAMADO
            partido.save()
        partido.refresh_from_db()
        with self.assertRaises(IntegrityError), transaction.atomic():
            partido.fecha_hora = timezone.now()  # sigue "pendiente"
            partido.save()

    def test_una_pareja_no_juega_contra_si_misma(self):
        partido, _ = _partido_de_grupo()
        partido.pareja_b = partido.pareja_a
        with self.assertRaises(ValidationError):
            partido.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            partido.save()

    def test_tipo_debe_ser_coherente_con_zona_llave_y_ronda(self):
        cat = crear_categoria()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Partido.objects.create(categoria=cat, tipo="grupo")  # grupo sin zona
        with self.assertRaises(IntegrityError), transaction.atomic():
            Partido.objects.create(categoria=cat, tipo="eliminatoria")  # sin llave ni ronda

    def test_eliminatoria_puede_tener_parejas_sin_definir(self):
        cat = crear_categoria()
        llave = Llave.objects.create(categoria=cat)
        p = Partido.objects.create(categoria=cat, tipo="eliminatoria", llave=llave, ronda="semifinal", numero=1)
        self.assertIsNone(p.pareja_a)
        p.full_clean()

    def test_no_se_repite_el_cruce_en_una_zona_ni_invertido(self):
        partido, _ = _partido_de_grupo()
        invertido = Partido(
            categoria=partido.categoria, tipo="grupo", zona=partido.zona,
            pareja_a=partido.pareja_b, pareja_b=partido.pareja_a,
        )
        with self.assertRaises(ValidationError):
            invertido.full_clean()

    def test_no_se_mezclan_categorias(self):
        partido, _ = _partido_de_grupo()
        intrusa = crear_inscripcion(crear_categoria("7ma LIBRE"))
        partido.pareja_b = intrusa
        with self.assertRaises(ValidationError):
            partido.full_clean()

    def test_no_se_borra_una_inscripcion_con_partidos(self):
        from django.db.models import RestrictedError

        partido, _ = _partido_de_grupo()
        with self.assertRaises(RestrictedError):
            partido.pareja_a.delete()

    def test_borrar_el_torneo_completo_si_funciona(self):
        partido, _ = _partido_de_grupo()
        partido.categoria.torneo.delete()
        self.assertEqual(Partido.objects.count(), 0)


def _resultado(partido, **extra):
    datos = dict(partido=partido, sets=[SET(6, 3), SET(6, 4)], origen="jugador")
    datos.update(extra)
    return Resultado.objects.create(**datos)


class ResultadoTests(TestCase):
    def setUp(self):
        self.partido, self.usuarios = _partido_de_grupo()

    def _oficializar(self, r, via=Resultado.VIA_CONFIRMACION_RIVAL, por=None):
        r.estado = Resultado.ESTADO_OFICIAL
        r.oficializado_en = timezone.now()
        r.oficializado_via = via
        r.oficializado_por = por
        r.save()

    def test_informado_por_jugador_NO_es_oficial_y_no_cuenta(self):
        r = _resultado(self.partido, informado_por=self.usuarios[0])
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_RESULTADO_INFORMADO)
        self.assertFalse(r.es_oficial)
        self.assertIsNone(self.partido.resultado_oficial)
        self.assertEqual(Resultado.objects.oficiales().count(), 0)

    def test_confirmacion_del_rival_lo_vuelve_oficial_y_finaliza(self):
        r = _resultado(self.partido, informado_por=self.usuarios[0])
        self._oficializar(r, por=self.usuarios[2])
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_FINALIZADO)
        self.assertEqual(self.partido.tipo_finalizacion, Partido.FIN_NORMAL)
        self.assertEqual(self.partido.resultado_oficial, r)
        self.assertEqual(list(Resultado.objects.oficiales()), [r])
        self.assertEqual(r.ganador, "a")

    def test_disputa_pasa_a_revision_y_sigue_sin_contar(self):
        r = _resultado(self.partido, informado_por=self.usuarios[0])
        r.estado = Resultado.ESTADO_DISPUTADO
        r.disputado_por = self.usuarios[2]
        r.disputado_en = timezone.now()
        r.motivo_disputa = "El segundo set fue 7-5 para nosotros"
        r.save()
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_RESULTADO_EN_REVISION)
        self.assertEqual(Resultado.objects.oficiales().count(), 0)

    def test_organizador_resuelve_la_disputa(self):
        org = crear_usuario()
        r = _resultado(self.partido, informado_por=self.usuarios[0])
        r.estado, r.disputado_por, r.disputado_en = Resultado.ESTADO_DISPUTADO, self.usuarios[2], timezone.now()
        r.save()
        r.sets = [SET(6, 3), SET(5, 7), SUPER(10, 8)]
        r.resuelto_por, r.resuelto_en, r.nota_resolucion = org, timezone.now(), "Verificado por WhatsApp"
        self._oficializar(r, via=Resultado.VIA_RESOLUCION_ORGANIZADOR, por=org)
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_FINALIZADO)
        self.assertTrue(r.resumen["super_tiebreak"])

    def test_carga_del_organizador_es_oficial_de_inmediato(self):
        org = crear_usuario()
        r = _resultado(
            self.partido, origen="organizador", estado="oficial", informado_por=org,
            oficializado_en=timezone.now(), oficializado_por=org, oficializado_via="carga_organizador",
        )
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_FINALIZADO)
        self.assertEqual(r.origen, "organizador")

    def test_la_base_impide_resultados_incoherentes(self):
        casos = {
            "oficial sin oficializar": dict(estado="oficial"),
            "no oficial pero con fecha de oficializacion": dict(oficializado_en=timezone.now(), oficializado_via="carga_organizador"),
            "organizador sin ser oficial": dict(origen="organizador", estado="informado"),
            "disputado sin fecha de disputa": dict(estado="disputado"),
        }
        for nombre, extra in casos.items():
            partido, _ = _partido_de_grupo()
            with self.subTest(nombre), self.assertRaises(IntegrityError), transaction.atomic():
                _resultado(partido, **extra)

    def test_un_solo_resultado_por_partido(self):
        _resultado(self.partido)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _resultado(self.partido)

    def test_sets_invalidos_se_rechazan_en_full_clean(self):
        r = Resultado(partido=self.partido, sets=[SET(6, 3), SET(4, 6)], origen="jugador")  # falta el 3.º
        with self.assertRaises(ValidationError):
            r.full_clean()

    def test_borrar_el_resultado_devuelve_el_partido_a_su_estado_previo(self):
        r = _resultado(self.partido)
        r.delete()
        self.partido.refresh_from_db()
        self.assertEqual(self.partido.estado, Partido.ESTADO_PENDIENTE)
        self.assertEqual(self.partido.tipo_finalizacion, "")

    def test_partido_finalizado_normal_exige_resultado_oficial(self):
        self.partido.fecha_hora = timezone.now()
        self.partido.estado = Partido.ESTADO_FINALIZADO
        self.partido.tipo_finalizacion = Partido.FIN_NORMAL
        with self.assertRaises(ValidationError):
            self.partido.full_clean()


class PropuestasYZonasTests(TestCase):
    def setUp(self):
        self.cat = crear_categoria()
        self.parejas = [crear_inscripcion(self.cat) for _ in range(4)]

    def _propuesta(self, nombre, distribucion):
        p = PropuestaFase.objects.create(
            categoria=self.cat, nombre=nombre, cantidad_zonas=len(distribucion),
            distribucion=distribucion, total_partidos=0,
        )
        return p

    def test_varias_propuestas_completas_conviven_sin_tocar_las_inscripciones(self):
        a = self._propuesta("Opción A", [2, 2])
        b = self._propuesta("Opción B", [4])
        za1, za2 = (PropuestaZona.objects.create(propuesta=a, nombre=n, orden=i) for i, n in enumerate("AB"))
        zb = PropuestaZona.objects.create(propuesta=b, nombre="A")
        for pareja, zona in zip(self.parejas, (za1, za1, za2, za2)):
            PropuestaAsignacion.objects.create(propuesta_zona=zona, inscripcion=pareja)
        for pareja in self.parejas:  # las mismas parejas, otra distribución
            PropuestaAsignacion.objects.create(propuesta_zona=zb, inscripcion=pareja)

        self.assertEqual(PropuestaAsignacion.objects.filter(propuesta=a).count(), 4)
        self.assertEqual(PropuestaAsignacion.objects.filter(propuesta=b).count(), 4)
        # Nada definitivo: ni zonas reales ni asignaciones a zona real.
        self.assertEqual(Zona.objects.count(), 0)
        self.assertEqual(ZonaPareja.objects.count(), 0)
        for pareja in self.parejas:
            self.assertFalse(hasattr(pareja, "zona_asignada"))

    def test_asignacion_completa_sola_la_propuesta(self):
        a = self._propuesta("Opción A", [4])
        z = PropuestaZona.objects.create(propuesta=a, nombre="A")
        asig = PropuestaAsignacion.objects.create(propuesta_zona=z, inscripcion=self.parejas[0])
        self.assertEqual(asig.propuesta_id, a.id)

    def test_una_pareja_no_puede_estar_dos_veces_en_la_misma_propuesta(self):
        a = self._propuesta("Opción A", [2, 2])
        z1 = PropuestaZona.objects.create(propuesta=a, nombre="A")
        z2 = PropuestaZona.objects.create(propuesta=a, nombre="B")
        PropuestaAsignacion.objects.create(propuesta_zona=z1, inscripcion=self.parejas[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            PropuestaAsignacion.objects.create(propuesta_zona=z2, inscripcion=self.parejas[0])

    def test_no_se_asigna_una_pareja_de_otra_categoria(self):
        a = self._propuesta("Opción A", [4])
        z = PropuestaZona.objects.create(propuesta=a, nombre="A")
        ajena = crear_inscripcion(crear_categoria("8va LIBRE"))
        asig = PropuestaAsignacion(propuesta_zona=z, inscripcion=ajena)
        with self.assertRaises(ValidationError):
            asig.full_clean()

    def test_solo_una_propuesta_seleccionada_por_competencia(self):
        a, b = self._propuesta("Opción A", [4]), self._propuesta("Opción B", [2, 2])
        a.seleccionada_en = timezone.now()
        a.save()
        b.seleccionada_en = timezone.now()
        with self.assertRaises(IntegrityError), transaction.atomic():
            b.save()

    def test_las_propuestas_de_otra_categoria_no_se_pisan(self):
        otra = crear_categoria("7ma LIBRE")
        self._propuesta("Opción A", [4])
        PropuestaFase.objects.create(categoria=otra, nombre="Opción A", cantidad_zonas=1, distribucion=[4])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._propuesta("Opción A", [4])  # mismo nombre en la misma categoría

    def test_zona_definitiva_una_pareja_una_zona(self):
        z1 = Zona.objects.create(categoria=self.cat, nombre="A")
        z2 = Zona.objects.create(categoria=self.cat, nombre="B")
        ZonaPareja.objects.create(zona=z1, inscripcion=self.parejas[0])
        with self.assertRaises(IntegrityError), transaction.atomic():
            ZonaPareja.objects.create(zona=z2, inscripcion=self.parejas[0])

    def test_zona_origen_recuerda_la_propuesta_aprobada_y_sobrevive_a_su_borrado(self):
        a = self._propuesta("Opción A", [4])
        z = Zona.objects.create(categoria=self.cat, nombre="A", propuesta_origen=a)
        a.delete()
        z.refresh_from_db()
        self.assertIsNone(z.propuesta_origen)


class AuditoriaTests(TestCase):
    def test_registra_quien_y_cuando(self):
        cat = crear_categoria()
        user = crear_usuario()
        ev = EventoAuditoria.objects.create(
            categoria=cat, accion="grupos_publicados", usuario=user, detalle={"propuesta": "Opción B"}
        )
        self.assertEqual(ev.usuario, user)
        self.assertIsNotNone(ev.creado_en)
        self.assertEqual(ev.detalle["propuesta"], "Opción B")


# ---------------------------------------------------------------------------
# Propuestas de zonas priorizadas por ranking
# ---------------------------------------------------------------------------
from decimal import Decimal  # noqa: E402

from django.db.models import ProtectedError  # noqa: E402

from rankings.models import VersionRanking  # noqa: E402
from rankings.servicios import nueva_version_desde, publicar_version, puntajes_de_pareja  # noqa: E402
from rankings.tests import entidad, persona, ranking_externo, temporada, version_con  # noqa: E402


class PropuestaConRankingTests(TestCase):
    def setUp(self):
        self.cat = crear_categoria("6ta LIBRE")
        self.org = entidad()
        self.t = temporada(ranking_externo(self.org))
        self.a, self.b, self.c = persona("27555000"), persona("30100001"), persona("31000111")
        self.v1 = version_con(self.t, [(self.a, 10), (self.b, 7)])

    def _propuesta(self, **extra):
        return PropuestaFase.objects.create(
            categoria=self.cat, nombre="Opción A", cantidad_zonas=1, distribucion=[2], **extra
        )

    def test_por_defecto_no_usa_ranking(self):
        p = self._propuesta()
        self.assertEqual((p.ranking_version, p.criterio_prioridad, p.puntajes_snapshot), (None, "disponibilidad", {}))

    def test_priorizar_por_ranking_exige_indicar_la_version_exacta(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._propuesta(criterio_prioridad=PropuestaFase.CRITERIO_RANKING)
        p = self._propuesta(criterio_prioridad=PropuestaFase.CRITERIO_RANKING, ranking_version=self.v1)
        self.assertEqual(p.ranking_version, self.v1)

    def test_la_distribucion_aprobada_no_cambia_retroactivamente_cuando_el_ranking_se_actualiza(self):
        calculo = puntajes_de_pareja(self.v1, self.a, self.b)
        p = self._propuesta(
            criterio_prioridad=PropuestaFase.CRITERIO_RANKING, ranking_version=self.v1,
            puntajes_snapshot={"version": self.v1.pk, "parejas": {"1": {"pareja": str(calculo["pareja"])}}},
        )
        # El organizador del ranking corrige los puntos y publica una versión nueva.
        borrador = nueva_version_desde(self.v1, self.org.usuario, "Actualización")
        fila = borrador.puntajes.get(persona=self.a)
        fila.puntos = Decimal("500")
        fila.save()
        v2 = publicar_version(borrador, self.org.usuario)

        p.refresh_from_db()
        self.assertEqual(p.ranking_version, self.v1)                               # sigue apuntando a la versión usada
        self.assertEqual(puntajes_de_pareja(p.ranking_version, self.a, self.b)["pareja"], Decimal("17.00"))
        self.assertEqual(puntajes_de_pareja(v2, self.a, self.b)["pareja"], Decimal("507.00"))  # la nueva, aparte
        self.assertEqual(p.puntajes_snapshot["parejas"]["1"]["pareja"], "17.00")   # y la foto no cambió

    def test_la_version_usada_por_una_propuesta_no_se_puede_borrar(self):
        borrador = VersionRanking.objects.create(temporada=temporada(self.t.ranking, "2027"))
        self._propuesta(ranking_version=borrador)
        with self.assertRaises(ProtectedError):
            borrador.delete()

    def test_una_pareja_con_un_integrante_sin_ranking_queda_marcada_y_sin_puntaje_inventado(self):
        p = self._propuesta(criterio_prioridad=PropuestaFase.CRITERIO_RANKING, ranking_version=self.v1)
        zona = PropuestaZona.objects.create(propuesta=p, nombre="A")
        inscripcion = crear_inscripcion(self.cat)
        calculo = puntajes_de_pareja(self.v1, self.a, self.c)   # c no figura: SR
        asignacion = PropuestaAsignacion.objects.create(
            propuesta_zona=zona, inscripcion=inscripcion, puntaje_pareja=calculo["pareja"],
            ranking_incompleto=calculo["ranking_incompleto"],
        )
        asignacion.refresh_from_db()
        self.assertEqual((asignacion.ranking_incompleto, asignacion.puntaje_pareja), (True, None))

    def test_la_base_impide_un_puntaje_de_pareja_en_una_pareja_con_ranking_incompleto(self):
        p = self._propuesta()
        zona = PropuestaZona.objects.create(propuesta=p, nombre="A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            PropuestaAsignacion.objects.create(
                propuesta_zona=zona, inscripcion=crear_inscripcion(self.cat), puntaje_pareja=Decimal("5"),
                ranking_incompleto=True,
            )

    def test_una_pareja_completa_guarda_su_puntaje(self):
        p = self._propuesta(criterio_prioridad=PropuestaFase.CRITERIO_RANKING, ranking_version=self.v1)
        zona = PropuestaZona.objects.create(propuesta=p, nombre="A")
        a = PropuestaAsignacion.objects.create(propuesta_zona=zona, inscripcion=crear_inscripcion(self.cat),
                                               puntaje_pareja=Decimal("17"), ranking_incompleto=False)
        a.refresh_from_db()
        self.assertEqual((a.puntaje_pareja, a.ranking_incompleto), (Decimal("17.00"), False))
