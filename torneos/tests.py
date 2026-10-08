import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Jugador

from .forms import InscripcionForm
from .models import Categoria, DisponibilidadInscripcion, Inscripcion
from .servicios import (
    normalizar_dni,
    registrar_disponibilidad_desde_texto,
    vincular_companero,
)
from .testing import crear_categoria, crear_inscripcion, crear_usuario


class NormalizarDniTests(TestCase):
    def test_quita_puntos_y_espacios(self):
        self.assertEqual(normalizar_dni("30.100.001"), "30100001")
        self.assertEqual(normalizar_dni(" 30 100 001 "), "30100001")
        self.assertEqual(normalizar_dni(None), "")


class VinculacionCompaneroTests(TestCase):
    def test_vincula_al_guardar_si_existe_cuenta_con_ese_dni(self):
        companero = crear_usuario(dni="27555000", categoria_oficial=6)
        insc = crear_inscripcion(crear_categoria(), dni_2="27.555.000")
        self.assertTrue(vincular_companero(insc))
        insc.refresh_from_db()
        self.assertEqual(insc.usuario_2, companero)

    def test_no_vincula_si_no_hay_cuenta(self):
        insc = crear_inscripcion(crear_categoria(), dni_2="99999999")
        self.assertFalse(vincular_companero(insc))
        insc.refresh_from_db()
        self.assertIsNone(insc.usuario_2)

    def test_no_vincula_si_el_dni_es_ambiguo(self):
        crear_usuario(dni="30.100.001")
        crear_usuario(dni="30100001")  # mismo DNI normalizado en otra cuenta
        insc = crear_inscripcion(crear_categoria(), dni_2="30100001")
        self.assertFalse(vincular_companero(insc))

    def test_no_vincula_a_uno_mismo(self):
        yo = crear_usuario(dni="30100001")
        insc = crear_inscripcion(crear_categoria(), usuario=yo, dni_2="30100001")
        self.assertFalse(vincular_companero(insc))


class DisponibilidadTests(TestCase):
    def test_texto_del_widget_se_convierte_en_filas(self):
        insc = crear_inscripcion(
            crear_categoria(), disponibilidad="Viernes de 18:00 a 22:00, Sábado de 10:00 a 15:00"
        )
        self.assertEqual(registrar_disponibilidad_desde_texto(insc), 2)
        filas = list(insc.disponibilidades.all())
        self.assertEqual([(f.dia, f.hora_desde.hour, f.hora_hasta.hour) for f in filas], [(4, 18, 22), (5, 10, 15)])

    def test_texto_historico_no_interpretable_se_ignora_sin_fallar(self):
        insc = crear_inscripcion(crear_categoria(), disponibilidad="los fines de semana a la tarde")
        self.assertEqual(registrar_disponibilidad_desde_texto(insc), 0)
        insc.refresh_from_db()
        self.assertEqual(insc.disponibilidad, "los fines de semana a la tarde")  # texto intacto

    def test_es_idempotente(self):
        insc = crear_inscripcion(crear_categoria(), disponibilidad="Lunes de 16:00 a 20:00")
        registrar_disponibilidad_desde_texto(insc)
        self.assertEqual(registrar_disponibilidad_desde_texto(insc), 0)
        self.assertEqual(insc.disponibilidades.count(), 1)

    def test_franja_invertida_se_ignora(self):
        insc = crear_inscripcion(crear_categoria(), disponibilidad="Lunes de 20:00 a 16:00")
        self.assertEqual(registrar_disponibilidad_desde_texto(insc), 0)

    def test_la_base_rechaza_hora_hasta_menor_o_igual(self):
        insc = crear_inscripcion(crear_categoria())
        with self.assertRaises(IntegrityError), transaction.atomic():
            DisponibilidadInscripcion.objects.create(
                inscripcion=insc, dia=0, hora_desde=datetime.time(20), hora_hasta=datetime.time(20)
            )


class InscripcionFormDatosDerivadosTests(TestCase):
    def test_guardar_el_form_vincula_companero_y_estructura_disponibilidad(self):
        companero = crear_usuario(dni="27555000", categoria_oficial=6)
        yo = crear_usuario(dni="30100001", categoria_oficial=6)
        cat = crear_categoria("6ta LIBRE")
        form = InscripcionForm(
            {
                "categoria": cat.id, "nombre_1": "Yo", "apellido_1": "Mismo", "dni_1": "30100001", "celular_1": "1",
                "localidad_1": "Tandil", "categoria_oficial_1": 6,
                "nombre_2": "Pedro", "apellido_2": "Prueba", "dni_2": "27.555.000", "localidad_2": "Tandil", "categoria_oficial_2": 6,
                "telefono": "1", "metodo_pago": "efectivo",
                "disponibilidad": "Lunes de 16:00 a 20:00",
            },
            torneo=cat.torneo, usuario=yo,
        )
        self.assertTrue(form.is_valid(), form.errors)
        insc = form.save(torneo=cat.torneo)
        insc.refresh_from_db()
        self.assertEqual(insc.usuario, yo)
        self.assertEqual(insc.usuario_2, companero)
        self.assertEqual(insc.disponibilidades.count(), 1)
        self.assertEqual(insc.disponibilidad, "Lunes de 16:00 a 20:00")  # texto viejo se conserva


class EstadoCategoriaTests(TestCase):
    """Tres ejes independientes: etapa / publicación de grupos / publicación de llave."""

    def _cat(self, **estado):
        cat = crear_categoria()
        for campo, valor in estado.items():
            setattr(cat, campo, valor)
        return cat

    def test_defaults_de_categorias_existentes(self):
        cat = crear_categoria()
        self.assertEqual(cat.etapa, Categoria.ETAPA_INSCRIPCION)
        self.assertEqual(cat.grupos_publicacion, Categoria.PUBLICACION_SIN_DEFINIR)
        self.assertEqual(cat.llave_publicacion, Categoria.PUBLICACION_SIN_DEFINIR)
        cat.full_clean()

    def test_grupos_terminados_esperando_publicar_la_llave_es_un_estado_valido(self):
        cat = self._cat(
            etapa=Categoria.ETAPA_GRUPOS_FINALIZADOS,
            grupos_publicacion=Categoria.PUBLICACION_PUBLICADA,
            llave_publicacion=Categoria.PUBLICACION_BORRADOR,
        )
        cat.full_clean()  # no lanza

    def test_borrador_de_grupos_durante_la_organizacion(self):
        self._cat(
            etapa=Categoria.ETAPA_ORGANIZACION_GRUPOS, grupos_publicacion=Categoria.PUBLICACION_BORRADOR
        ).full_clean()

    def test_no_se_puede_publicar_grupos_antes_de_jugar(self):
        cat = self._cat(
            etapa=Categoria.ETAPA_ORGANIZACION_GRUPOS, grupos_publicacion=Categoria.PUBLICACION_PUBLICADA
        )
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_no_hay_propuesta_elegida_en_inscripcion(self):
        cat = self._cat(grupos_publicacion=Categoria.PUBLICACION_BORRADOR)
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_no_hay_llave_mientras_se_juegan_los_grupos(self):
        cat = self._cat(
            etapa=Categoria.ETAPA_JUEGO_GRUPOS,
            grupos_publicacion=Categoria.PUBLICACION_PUBLICADA,
            llave_publicacion=Categoria.PUBLICACION_BORRADOR,
        )
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_no_se_publica_la_llave_si_los_grupos_no_estan_publicados(self):
        cat = self._cat(
            etapa=Categoria.ETAPA_JUEGO_LLAVE,
            grupos_publicacion=Categoria.PUBLICACION_BORRADOR,
            llave_publicacion=Categoria.PUBLICACION_PUBLICADA,
        )
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_categorias_de_un_mismo_torneo_avanzan_independientes(self):
        a = crear_categoria("5ta LIBRE")
        b = Categoria.objects.create(torneo=a.torneo, nombre="6ta LIBRE", cupo_minimo=1)
        a.etapa = Categoria.ETAPA_JUEGO_LLAVE
        a.grupos_publicacion = a.llave_publicacion = Categoria.PUBLICACION_PUBLICADA
        a.save()
        b.refresh_from_db()
        self.assertEqual(b.etapa, Categoria.ETAPA_INSCRIPCION)


# ---------------------------------------------------------------------------
# Categoría oficial: del jugador, del compañero, e historia de las inscripciones
# ---------------------------------------------------------------------------
from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from accounts.models import Identidad, puede_subir_categoria  # noqa: E402


def _datos(cat, yo, **extra):
    """POST/form data de una inscripción válida; `yo` es quien inscribe."""
    datos = {
        "categoria": cat.id, "nombre_1": "Yo", "apellido_1": "Mismo", "dni_1": yo.identidad.dni, "celular_1": "1",
        "localidad_1": "Tandil", "categoria_oficial_1": 6,
        "nombre_2": "Compañero", "apellido_2": "Prueba", "dni_2": "20999111", "localidad_2": "Tandil", "categoria_oficial_2": 6,
        "telefono": "1", "metodo_pago": "efectivo", "disponibilidad": "",
    }
    datos.update(extra)
    return datos


def _form(cat, yo, **extra):
    return InscripcionForm(_datos(cat, yo, **extra), torneo=cat.torneo, usuario=yo)


def _cliente(usuario):
    c = Client()
    c.login(username=usuario.username, password="x")
    return c


class ReglaSoloSubirTests(TestCase):
    def test_tabla_de_la_regla(self):
        permitidos = [(8, 7), (7, 6), (6, 5), (6, 4), (6, 6), (None, 8), (None, 1)]
        prohibidos = [(6, 7), (6, 8), (5, 6), (1, 2)]
        for actual, nueva in permitidos:
            self.assertTrue(puede_subir_categoria(actual, nueva), (actual, nueva))
        for actual, nueva in prohibidos:
            self.assertFalse(puede_subir_categoria(actual, nueva), (actual, nueva))


class CategoriaPropiaTests(TestCase):
    """Puntos 1 y 2: la categoría del jugador que inscribe."""

    def test_primera_inscripcion_exige_declararla_y_se_guarda_en_el_perfil(self):
        yo = crear_usuario(dni="30100001")  # sin categoría todavía
        cat = crear_categoria("6ta LIBRE")
        self.assertFalse(_form(cat, yo, categoria_oficial_1="").is_valid())  # no hay default silencioso

        r = _cliente(yo).post(f"/torneos/{cat.torneo.codigo}/", _datos(cat, yo, categoria_oficial_1=6))
        self.assertEqual(r.status_code, 302)
        yo.jugador.refresh_from_db()
        self.assertEqual(yo.jugador.categoria_oficial, 6)

    def test_en_futuras_inscripciones_se_recupera_la_registrada(self):
        yo = crear_usuario(dni="30100001", categoria_oficial=7)
        cat = crear_categoria("7ma LIBRE")
        pagina = _cliente(yo).get(f"/torneos/{cat.torneo.codigo}/")
        self.assertContains(pagina, "Podés mejorarla, pero no bajarla manualmente.")
        form = InscripcionForm(torneo=cat.torneo, usuario=yo)
        self.assertEqual(form.fields["categoria_oficial_1"].initial, 7)

    def test_subir_durante_la_inscripcion_actualiza_perfil_y_se_usa_para_validar(self):
        yo = crear_usuario(dni="30100001", categoria_oficial=7)
        cat = crear_categoria("6ta LIBRE")  # torneo que NO permite subir categorías
        # Con 7ma + compañero 7ma no podrían anotarse en 6ta: solo vale porque ahora es 6ta.
        datos = _datos(cat, yo, categoria_oficial_1=6, categoria_oficial_2=7)
        r = _cliente(yo).post(f"/torneos/{cat.torneo.codigo}/", datos)
        self.assertEqual(r.status_code, 302)
        yo.jugador.refresh_from_db()
        self.assertEqual(yo.jugador.categoria_oficial, 6)

    def test_no_se_puede_bajar_y_el_perfil_no_cambia(self):
        yo = crear_usuario(dni="30100001", categoria_oficial=6)
        cat = crear_categoria("7ma LIBRE")
        form = _form(cat, yo, categoria_oficial_1=7, categoria_oficial_2=7)
        self.assertFalse(form.is_valid())
        self.assertIn("categoria_oficial_1", form.errors)

        r = _cliente(yo).post(f"/torneos/{cat.torneo.codigo}/", _datos(cat, yo, categoria_oficial_1=7, categoria_oficial_2=7))
        self.assertEqual(r.status_code, 200)  # vuelve al formulario con el error
        self.assertEqual(Inscripcion.objects.count(), 0)
        yo.jugador.refresh_from_db()
        self.assertEqual(yo.jugador.categoria_oficial, 6)

    def test_las_inscripciones_historicas_conservan_la_categoria_de_ese_momento(self):
        yo = crear_usuario(dni="30100001", categoria_oficial=7)
        cat_vieja = crear_categoria("7ma LIBRE")
        vieja = crear_inscripcion(cat_vieja, usuario=yo, categoria_oficial_1=7)
        cat_nueva = crear_categoria("6ta LIBRE")
        _cliente(yo).post(f"/torneos/{cat_nueva.torneo.codigo}/", _datos(cat_nueva, yo, categoria_oficial_1=6))
        yo.jugador.refresh_from_db()
        vieja.refresh_from_db()
        self.assertEqual(yo.jugador.categoria_oficial, 6)  # perfil: la actual
        self.assertEqual(vieja.categoria_oficial_1, 7)     # historia: la de entonces


class CategoriaCompaneroTests(TestCase):
    """Punto 3: la categoría del compañero la decide su perfil, no quien inscribe."""

    def setUp(self):
        self.yo = crear_usuario(dni="30100001", categoria_oficial=6)
        self.cat = crear_categoria("6ta LIBRE")

    def test_con_cuenta_y_categoria_se_usa_la_de_su_perfil_aunque_envien_otra(self):
        companero = crear_usuario(dni="27555000", categoria_oficial=6)
        # Quien inscribe intenta "bajarlo" a 8va para entrar a una categoría inferior.
        form = _form(self.cat, self.yo, dni_2="27.555.000", categoria_oficial_2=8)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria_oficial_2"], 6)
        insc = form.save(torneo=self.cat.torneo)
        self.assertEqual(insc.categoria_oficial_2, 6)
        self.assertEqual(insc.usuario_2, companero)
        companero.jugador.refresh_from_db()
        self.assertEqual(companero.jugador.categoria_oficial, 6)  # su perfil, intacto

    def test_la_categoria_forzada_es_la_que_valida_la_inscripcion(self):
        """Caso donde el truco SÍ funcionaría si el servidor confiara en el navegador:
        yo=7ma y compañero real=5ta -> la pareja es 5ta y NO puede anotarse en 7ma.
        Si le "bajan" al compañero a 8va, la base sería 7ma y entraría."""
        yo7 = crear_usuario(dni="30100055", categoria_oficial=7)
        crear_usuario(dni="27555000", categoria_oficial=5)
        cat7 = crear_categoria("7ma LIBRE")
        form = _form(cat7, yo7, categoria_oficial_1=7, dni_2="27555000", categoria_oficial_2=8)
        self.assertFalse(form.is_valid())
        self.assertIn("categoria", form.errors)
        r = _cliente(yo7).post(
            f"/torneos/{cat7.torneo.codigo}/", _datos(cat7, yo7, categoria_oficial_1=7, dni_2="27555000", categoria_oficial_2=8)
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Inscripcion.objects.count(), 0)

    def test_sin_cuenta_la_categoria_es_declarada_solo_para_la_inscripcion(self):
        form = _form(self.cat, self.yo, dni_2="99999999", categoria_oficial_2=6)
        self.assertTrue(form.is_valid(), form.errors)
        insc = form.save(torneo=self.cat.torneo)
        self.assertIsNone(insc.usuario_2)
        self.assertEqual(insc.categoria_oficial_2, 6)

    def test_sin_cuenta_y_sin_declarar_se_rechaza(self):
        form = _form(self.cat, self.yo, dni_2="99999999", categoria_oficial_2="")
        self.assertFalse(form.is_valid())
        self.assertIn("categoria_oficial_2", form.errors)

    def test_con_cuenta_pero_sin_perfil_de_jugador_no_hay_donde_guardarla(self):
        User = get_user_model()
        u = User.objects.create_user(username="solo_org", password="x")
        Identidad.objects.create(usuario=u, dni="27555000", localidad="Tandil")  # sin Jugador
        r = _cliente(self.yo).post(
            f"/torneos/{self.cat.torneo.codigo}/", _datos(self.cat, self.yo, dni_2="27555000", categoria_oficial_2=6)
        )
        self.assertEqual(r.status_code, 302)  # se inscribe igual, con la categoría declarada
        self.assertEqual(Inscripcion.objects.get().categoria_oficial_2, 6)

    def test_dni_ambiguo_se_trata_como_sin_cuenta(self):
        crear_usuario(dni="27.555.000", categoria_oficial=5)
        crear_usuario(dni="27555000", categoria_oficial=8)
        form = _form(self.cat, self.yo, dni_2="27555000", categoria_oficial_2=6)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria_oficial_2"], 6)  # no se adivina de quién es

    def test_no_se_puede_inscribir_como_companero_a_uno_mismo(self):
        self.assertFalse(_form(self.cat, self.yo, dni_2="30.100.001").is_valid())  # mismo DNI tipeado
        otro_yo = crear_usuario(dni="30100099", categoria_oficial=6)
        form = _form(self.cat, self.yo, dni_1="1", dni_2=otro_yo.identidad.dni)
        self.assertTrue(form.is_valid(), form.errors)  # una persona distinta sí


class ConsultaCompaneroTests(TestCase):
    def setUp(self):
        self.yo = crear_usuario(dni="30100001", categoria_oficial=6)
        self.c = _cliente(self.yo)
        self.url = "/torneos/companero/"

    def test_requiere_sesion(self):
        self.assertEqual(Client().get(self.url, {"dni": "27555000"}).status_code, 401)

    def test_con_categoria_registrada(self):
        crear_usuario(dni="27555000", categoria_oficial=5)
        self.assertEqual(
            self.c.get(self.url, {"dni": "27.555.000"}).json(),
            {"tiene_cuenta": True, "categoria_oficial": 5, "bloqueada": True},
        )

    def test_con_cuenta_sin_categoria(self):
        crear_usuario(dni="27555000")
        self.assertEqual(
            self.c.get(self.url, {"dni": "27555000"}).json(),
            {"tiene_cuenta": True, "categoria_oficial": None, "bloqueada": False},
        )

    def test_sin_cuenta_y_no_filtra_datos_personales(self):
        d = self.c.get(self.url, {"dni": "99999999"}).json()
        self.assertEqual(d, {"tiene_cuenta": False, "categoria_oficial": None, "bloqueada": False})
        crear_usuario(dni="27555000", categoria_oficial=5)
        self.assertEqual(
            set(self.c.get(self.url, {"dni": "27555000"}).json()), {"tiene_cuenta", "categoria_oficial", "bloqueada"}
        )  # ni nombre, ni usuario, ni localidad

    def test_su_propio_dni(self):
        self.assertEqual(self.c.get(self.url, {"dni": "30100001"}).json(), {"error": "mismo_usuario"})


class ActualizarCategoriaDesdeMiCuentaTests(TestCase):
    URL = "/cuenta/categoria/"

    def _post(self, usuario, valor):
        return _cliente(usuario).post(self.URL, {"categoria_oficial": valor})

    def test_se_puede_subir(self):
        u = crear_usuario(dni="30100001", categoria_oficial=7)
        self._post(u, 6)
        u.jugador.refresh_from_db()
        self.assertEqual(u.jugador.categoria_oficial, 6)

    def test_no_se_puede_bajar_ni_con_un_post_armado_a_mano(self):
        u = crear_usuario(dni="30100001", categoria_oficial=6)
        for valor in (7, 8):
            self._post(u, valor)
        u.jugador.refresh_from_db()
        self.assertEqual(u.jugador.categoria_oficial, 6)

    def test_valores_invalidos_no_cambian_nada(self):
        u = crear_usuario(dni="30100001", categoria_oficial=6)
        for valor in ("", "abc", 0, 99):
            self._post(u, valor)
        u.jugador.refresh_from_db()
        self.assertEqual(u.jugador.categoria_oficial, 6)

    def test_la_primera_categoria_se_declara_al_inscribirse_no_aca(self):
        u = crear_usuario(dni="30100001")
        self._post(u, 5)
        u.jugador.refresh_from_db()
        self.assertIsNone(u.jugador.categoria_oficial)

    def test_la_pantalla_solo_ofrece_categorias_superiores(self):
        u = crear_usuario(dni="30100001", categoria_oficial=6)
        pagina = _cliente(u).get("/cuenta/mi-cuenta/")
        self.assertContains(pagina, "Categoría oficial")
        opciones = [valor for valor, _ in pagina.context["categorias_para_subir"]]
        self.assertEqual(opciones, [1, 2, 3, 4, 5])

    def test_cambiar_la_categoria_no_toca_inscripciones_anteriores(self):
        u = crear_usuario(dni="30100001", categoria_oficial=7)
        vieja = crear_inscripcion(crear_categoria("7ma LIBRE"), usuario=u, categoria_oficial_1=7)
        self._post(u, 5)
        vieja.refresh_from_db()
        self.assertEqual(vieja.categoria_oficial_1, 7)

    def test_requiere_login_y_metodo_post(self):
        self.assertEqual(Client().post(self.URL, {"categoria_oficial": 5}).status_code, 302)
        u = crear_usuario(dni="30100001", categoria_oficial=6)
        self.assertEqual(_cliente(u).get(self.URL).status_code, 405)


# ---------------------------------------------------------------------------
# Caso 1: categoría declarada por un tercero, pendiente de confirmación del titular
# ---------------------------------------------------------------------------
from accounts.models import Organizador  # noqa: E402
from .models import RevisionIdentidad  # noqa: E402
from .servicios import (  # noqa: E402
    buscar_companero,
    categoria_minima_declarada,
    registrar_revision_dni,
    resolver_revision,
)


def _declarar(companero, categoria, estado=Inscripcion.ESTADO_CONFIRMADA, **extra):
    """Una inscripción (de otra persona) donde se declaró `categoria` para `companero`."""
    otro = crear_usuario(dni=f"3{next(iter([abs(hash(str(companero.pk) + str(categoria) + estado)) % 10**7]))}")
    return crear_inscripcion(
        crear_categoria(), usuario=otro, usuario_2=companero, categoria_oficial_2=categoria,
        dni_2=companero.identidad.dni, estado=estado, **extra,
    )


class CategoriaDeclaradaPorTercerosTests(TestCase):
    CONFIRMAR = "/cuenta/categoria/"

    def setUp(self):
        self.yo = crear_usuario(dni="30100001", categoria_oficial=6)
        self.cat = crear_categoria("6ta LIBRE")
        self.companero = crear_usuario(dni="27555000")  # tiene cuenta, SIN categoría oficial

    def _inscribir(self, **extra):
        return _cliente(self.yo).post(
            f"/torneos/{self.cat.torneo.codigo}/",
            _datos(self.cat, self.yo, dni_2="27555000", categoria_oficial_2=6, **extra),
        )

    def test_lo_que_declara_un_tercero_NO_pasa_a_ser_categoria_oficial_del_perfil(self):
        self.assertEqual(self._inscribir().status_code, 302)
        self.companero.jugador.refresh_from_db()
        self.assertIsNone(self.companero.jugador.categoria_oficial)
        insc = Inscripcion.objects.get()
        self.assertEqual(insc.categoria_oficial_2, 6)         # sí queda en la inscripción
        self.assertEqual(insc.usuario_2, self.companero)      # y vinculada a su cuenta

    def test_la_declaracion_pendiente_no_fija_piso_hasta_que_el_organizador_confirma(self):
        self._inscribir()  # queda PENDIENTE de pago/habilitación
        self.assertIsNone(categoria_minima_declarada(self.companero))
        Inscripcion.objects.update(estado=Inscripcion.ESTADO_CONFIRMADA)
        self.assertEqual(categoria_minima_declarada(self.companero), 6)

    def test_declaraciones_rechazadas_no_cuentan(self):
        _declarar(self.companero, 1, estado=Inscripcion.ESTADO_RECHAZADA)
        self.assertIsNone(categoria_minima_declarada(self.companero))

    def test_mi_cuenta_muestra_lo_declarado_y_pide_confirmacion(self):
        _declarar(self.companero, 7)
        _declarar(self.companero, 6)
        pagina = _cliente(self.companero).get("/cuenta/mi-cuenta/")
        ctx = pagina.context
        self.assertEqual(ctx["categoria_piso"], 6)  # la más alta de las declaradas
        self.assertEqual(len(ctx["declaradas_por_terceros"]), 2)
        self.assertEqual([v for v, _ in ctx["categorias_para_confirmar"]], [1, 2, 3, 4, 5, 6])
        self.assertContains(pagina, "Confirmar categoría")

    def test_el_titular_puede_confirmar_o_subir_pero_nunca_bajar_del_piso(self):
        _declarar(self.companero, 7)
        _declarar(self.companero, 6)  # categoría válida más alta ya registrada: 6ta
        c = _cliente(self.companero)

        c.post(self.CONFIRMAR, {"categoria_oficial": 7})   # inferior al piso
        self.companero.jugador.refresh_from_db()
        self.assertIsNone(self.companero.jugador.categoria_oficial)

        c.post(self.CONFIRMAR, {"categoria_oficial": 6})   # confirma la declarada
        self.companero.jugador.refresh_from_db()
        self.assertEqual(self.companero.jugador.categoria_oficial, 6)

    def test_el_titular_puede_declarar_una_superior(self):
        _declarar(self.companero, 6)
        _cliente(self.companero).post(self.CONFIRMAR, {"categoria_oficial": 5})
        self.companero.jugador.refresh_from_db()
        self.assertEqual(self.companero.jugador.categoria_oficial, 5)

    def test_sin_ninguna_declaracion_previa_sigue_rigiendo_la_primera_inscripcion(self):
        _cliente(self.companero).post(self.CONFIRMAR, {"categoria_oficial": 5})
        self.companero.jugador.refresh_from_db()
        self.assertIsNone(self.companero.jugador.categoria_oficial)

    def test_un_segundo_tercero_no_puede_declarar_algo_inferior_a_lo_ya_declarado(self):
        _declarar(self.companero, 6)
        otro = crear_usuario(dni="30100077", categoria_oficial=6)
        inferior = _form(crear_categoria("6ta LIBRE"), otro, dni_2="27555000", categoria_oficial_2=8)
        self.assertFalse(inferior.is_valid())
        self.assertIn("categoria_oficial_2", inferior.errors)
        for permitido, torneo_cat in ((6, "6ta LIBRE"), (5, "5ta LIBRE")):  # igual o superior a lo ya declarado
            form = _form(crear_categoria(torneo_cat), otro, dni_2="27555000", categoria_oficial_2=permitido)
            self.assertTrue(form.is_valid(), form.errors)
        self.companero.jugador.refresh_from_db()
        self.assertIsNone(self.companero.jugador.categoria_oficial)

    def test_cuando_ya_tiene_categoria_oficial_siguen_mandando_su_perfil(self):
        self.companero.jugador.categoria_oficial = 5
        self.companero.jugador.save()
        cat5 = crear_categoria("5ta LIBRE")  # la pareja (6ta + 5ta) es de 5ta
        form = _form(cat5, self.yo, dni_2="27555000", categoria_oficial_2=8)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria_oficial_2"], 5)

    def test_el_titular_al_inscribirse_tampoco_puede_registrar_algo_inferior(self):
        _declarar(self.companero, 6)
        cat = crear_categoria("6ta LIBRE")
        self.assertEqual(InscripcionForm(torneo=cat.torneo, usuario=self.companero).fields["categoria_oficial_1"].initial, 6)
        self.assertFalse(_form(cat, self.companero, categoria_oficial_1=7, categoria_oficial_2=7).is_valid())

        cat5 = crear_categoria("5ta LIBRE")
        r = _cliente(self.companero).post(
            f"/torneos/{cat5.torneo.codigo}/", _datos(cat5, self.companero, categoria_oficial_1=5, categoria_oficial_2=5)
        )
        self.assertEqual(r.status_code, 302)
        self.companero.jugador.refresh_from_db()
        self.assertEqual(self.companero.jugador.categoria_oficial, 5)  # eligió una superior

    def test_confirmar_o_cambiar_la_oficial_no_toca_la_inscripcion_historica(self):
        insc = _declarar(self.companero, 7)
        _cliente(self.companero).post(self.CONFIRMAR, {"categoria_oficial": 6})
        _cliente(self.companero).post(self.CONFIRMAR, {"categoria_oficial": 4})
        insc.refresh_from_db()
        self.assertEqual(insc.categoria_oficial_2, 7)  # lo que se usó en ese torneo

    def test_el_endpoint_informa_el_piso_solo_cuando_existe(self):
        c = _cliente(self.yo)
        self.assertEqual(c.get("/torneos/companero/", {"dni": "27555000"}).json(),
                         {"tiene_cuenta": True, "categoria_oficial": None, "bloqueada": False})
        _declarar(self.companero, 6)
        self.assertEqual(c.get("/torneos/companero/", {"dni": "27555000"}).json(),
                         {"tiene_cuenta": True, "categoria_oficial": None, "bloqueada": False, "categoria_minima": 6})


# ---------------------------------------------------------------------------
# Caso 2: cuenta con perfil solo de Organizador que participa como jugador
# ---------------------------------------------------------------------------
def _solo_organizador(dni="27111222", username="solo_org2"):
    User = get_user_model()
    u = User.objects.create_user(username=username, password="x", first_name="Olga", last_name="Org")
    Identidad.objects.create(usuario=u, dni=dni, localidad="Tandil", ultimo_perfil="organizador")
    Organizador.objects.create(usuario=u, rol="liga", nombre_liga="Liga Olga", celular="555")
    return u


class OrganizadorQueJuegaTests(TestCase):
    def setUp(self):
        self.org = _solo_organizador()
        self.cat = crear_categoria("6ta LIBRE")

    def _inscribir(self, **extra):
        return _cliente(self.org).post(
            f"/torneos/{self.cat.torneo.codigo}/", _datos(self.cat, self.org, **extra)
        )

    def test_la_pantalla_de_inscripcion_funciona_sin_perfil_de_jugador(self):
        self.assertFalse(hasattr(self.org, "jugador"))
        self.assertEqual(_cliente(self.org).get(f"/torneos/{self.cat.torneo.codigo}/").status_code, 200)

    def test_al_inscribirse_se_activa_el_perfil_de_jugador_sin_perder_el_de_organizador(self):
        self.assertEqual(self._inscribir().status_code, 302)
        u = get_user_model().objects.get(pk=self.org.pk)
        self.assertEqual(u.jugador.categoria_oficial, 6)        # ahora sí se guarda su categoría
        self.assertEqual(u.jugador.celular, "1")
        self.assertEqual(u.organizador.nombre_liga, "Liga Olga")  # el rol de Organizador intacto
        self.assertEqual(u.organizador.celular, "555")
        self.assertEqual(u.identidad.ultimo_perfil, "organizador")  # no se le cambia el perfil activo

    def test_ambos_perfiles_quedan_disponibles_en_la_cuenta(self):
        self._inscribir()
        pagina = _cliente(self.org).get("/cuenta/mi-cuenta/")
        self.assertTrue(pagina.context["tiene_jugador"] and pagina.context["tiene_organizador"])

    def test_una_segunda_inscripcion_no_duplica_el_perfil(self):
        self._inscribir()
        cat2 = crear_categoria("6ta LIBRE")
        r = _cliente(self.org).post(f"/torneos/{cat2.torneo.codigo}/", _datos(cat2, self.org))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Jugador.objects.filter(usuario=self.org).count(), 1)

    def test_tambien_puede_activar_el_perfil_de_jugador_desde_mi_cuenta(self):
        r = _cliente(self.org).post("/cuenta/mi-cuenta/", {"accion": "activar_jugador", "celular": "999"})
        self.assertEqual(r.status_code, 302)
        u = get_user_model().objects.get(pk=self.org.pk)
        self.assertEqual(u.jugador.celular, "999")
        self.assertTrue(hasattr(u, "organizador"))

    def test_sin_perfil_de_jugador_igual_se_respeta_lo_ya_declarado_antes(self):
        """Declaró 5ta en una inscripción previa confirmada (antes de tener perfil de
        Jugador): no puede ahora registrar una inferior."""
        crear_inscripcion(crear_categoria("5ta LIBRE"), usuario=self.org, categoria_oficial_1=5)
        form = _form(self.cat, self.org, categoria_oficial_1=7, categoria_oficial_2=7)
        self.assertFalse(form.is_valid())
        self.assertIn("categoria_oficial_1", form.errors)

    def test_como_companero_la_categoria_declarada_espera_su_confirmacion(self):
        """Si es el compañero, no hay perfil de Jugador donde confirmar todavía:
        la declaración queda en la inscripción y Mi cuenta le avisa."""
        yo = crear_usuario(dni="30100001", categoria_oficial=6)
        r = _cliente(yo).post(
            f"/torneos/{self.cat.torneo.codigo}/",
            _datos(self.cat, yo, dni_2="27111222", nombre_2="Olga", apellido_2="Org", categoria_oficial_2=6),
        )
        self.assertEqual(r.status_code, 302)
        self.assertFalse(hasattr(get_user_model().objects.get(pk=self.org.pk), "jugador"))
        Inscripcion.objects.update(estado=Inscripcion.ESTADO_CONFIRMADA)
        pagina = _cliente(self.org).get("/cuenta/mi-cuenta/")
        self.assertContains(pagina, "Activá tu perfil de Jugador")
        self.assertEqual(len(pagina.context["declaradas_por_terceros"]), 1)


# ---------------------------------------------------------------------------
# Caso 3: DNI duplicado o ambiguo -> no se vincula, no se usa ninguna categoría, se revisa
# ---------------------------------------------------------------------------
class DniAmbiguoTests(TestCase):
    def setUp(self):
        self.yo = crear_usuario(dni="30100001", categoria_oficial=6)
        self.cat = crear_categoria("6ta LIBRE")
        # Dos cuentas "equivalentes" (datos viejos): mismo DNI normalizado, categorías distintas.
        self.a = crear_usuario(dni="27.555.000", categoria_oficial=5)
        self.b = crear_usuario(dni="27555000", categoria_oficial=8)

    def _inscribir(self, **extra):
        return _cliente(self.yo).post(
            f"/torneos/{self.cat.torneo.codigo}/",
            _datos(self.cat, self.yo, dni_2="27555000", categoria_oficial_2=6, **extra),
        )

    def test_no_se_usa_la_categoria_de_ninguna_de_las_cuentas(self):
        c = buscar_companero("27555000")
        self.assertTrue(c.ambiguo)
        self.assertFalse(c.tiene_cuenta)
        self.assertIsNone(c.categoria)
        self.assertFalse(c.categoria_bloqueada)
        form = _form(self.cat, self.yo, dni_2="27555000", categoria_oficial_2=6)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria_oficial_2"], 6)  # ni 5 ni 8

    def test_no_se_vincula_a_ninguna_cuenta_y_queda_el_caso_marcado_para_revision(self):
        self.assertEqual(self._inscribir().status_code, 302)
        insc = Inscripcion.objects.get()
        self.assertIsNone(insc.usuario_2)
        caso = RevisionIdentidad.objects.get()
        self.assertEqual(caso.estado, RevisionIdentidad.ESTADO_ABIERTA)
        self.assertEqual(caso.dni, "27555000")
        self.assertEqual(set(caso.cuentas.all()), {self.a, self.b})
        self.assertEqual(list(caso.inscripciones.all()), [insc])

    def test_no_se_duplican_los_casos_ni_las_cuentas(self):
        self._inscribir()
        cat2 = crear_categoria("6ta LIBRE")
        _cliente(self.yo).post(f"/torneos/{cat2.torneo.codigo}/", _datos(cat2, self.yo, dni_2="27.555.000", categoria_oficial_2=6))
        self.assertEqual(RevisionIdentidad.objects.count(), 1)  # un solo caso abierto por DNI
        caso = RevisionIdentidad.objects.get()
        self.assertEqual(caso.cuentas.count(), 2)
        self.assertEqual(caso.inscripciones.count(), 2)

    def test_las_categorias_oficiales_de_esas_cuentas_no_se_tocan(self):
        self._inscribir()
        self.a.jugador.refresh_from_db(); self.b.jugador.refresh_from_db()
        self.assertEqual((self.a.jugador.categoria_oficial, self.b.jugador.categoria_oficial), (5, 8))

    def test_el_endpoint_avisa_ambiguo_sin_revelar_datos(self):
        d = _cliente(self.yo).get("/torneos/companero/", {"dni": "27555000"}).json()
        self.assertEqual(d, {"tiene_cuenta": False, "categoria_oficial": None, "bloqueada": False, "ambiguo": True})

    def test_no_se_pueden_crear_cuentas_nuevas_con_un_dni_equivalente(self):
        for dni in ("27.555.000", "27555000", " 27 555 000 "):
            r = Client().post("/registro/", {
                "nombre": "Dup", "apellido": "Licado", "dni": dni, "localidad": "Tandil", "rol": "jugador",
                "celular": "1", "username": "dup_" + str(abs(hash(dni)) % 1000),
                "password1": "clave12345", "password2": "clave12345",
            })
            self.assertEqual(r.status_code, 200, dni)  # vuelve al formulario con el error
        self.assertFalse(get_user_model().objects.filter(username__startswith="dup_").exists())

    def test_editar_el_dni_en_la_inscripcion_no_permite_tomar_uno_equivalente_al_de_otra_cuenta(self):
        _cliente(self.yo).post(
            f"/torneos/{self.cat.torneo.codigo}/", _datos(self.cat, self.yo, dni_1="27.555.000", dni_2="20111333")
        )
        self.yo.identidad.refresh_from_db()
        self.assertEqual(self.yo.identidad.dni, "30100001")  # no se pisó

    def test_mi_cuenta_avisa_que_el_dni_esta_en_revision(self):
        self._inscribir()
        self.assertContains(_cliente(self.a).get("/cuenta/mi-cuenta/"), "Tu DNI está en revisión")
        self.assertNotContains(_cliente(self.yo).get("/cuenta/mi-cuenta/"), "Tu DNI está en revisión")


class ResolverRevisionTests(TestCase):
    def setUp(self):
        self.a = crear_usuario(dni="27.555.000", categoria_oficial=5)
        self.b = crear_usuario(dni="27555000", categoria_oficial=8)
        self.staff = crear_usuario()
        self.i1 = crear_inscripcion(crear_categoria(), dni_2="27555000", categoria_oficial_2=7)
        self.i2 = crear_inscripcion(crear_categoria(), dni_2="27.555.000", categoria_oficial_2=6)
        self.sin_flag = crear_inscripcion(crear_categoria(), dni_2="27 555 000", categoria_oficial_2=8)  # nunca marcada
        self.otra = crear_inscripcion(crear_categoria(), dni_2="11111111")
        self.caso = registrar_revision_dni("27555000", [self.a, self.b], [self.i1, self.i2])

    def test_con_titular_verificado_se_vinculan_sus_inscripciones_y_nada_mas(self):
        n = resolver_revision(self.caso, self.a, self.staff, "Verificado con DNI físico")
        self.assertEqual(n, 3)
        for insc in (self.i1, self.i2, self.sin_flag):
            insc.refresh_from_db()
            self.assertEqual(insc.usuario_2, self.a)
        self.otra.refresh_from_db()
        self.assertIsNone(self.otra.usuario_2)
        self.caso.refresh_from_db()
        self.assertEqual(self.caso.estado, RevisionIdentidad.ESTADO_RESUELTA)
        self.assertEqual((self.caso.titular_verificado, self.caso.resuelta_por), (self.a, self.staff))
        self.assertIsNotNone(self.caso.resuelta_en)

    def test_las_categorias_declaradas_historicas_se_conservan_al_resolver(self):
        resolver_revision(self.caso, self.a, self.staff, "ok")
        self.assertEqual(
            [Inscripcion.objects.get(pk=i.pk).categoria_oficial_2 for i in (self.i1, self.i2, self.sin_flag)], [7, 6, 8]
        )
        self.a.jugador.refresh_from_db()
        self.assertEqual(self.a.jugador.categoria_oficial, 5)  # y el perfil tampoco cambió

    def test_ninguna_cuenta_corresponde_cierra_sin_vincular(self):
        self.assertEqual(resolver_revision(self.caso, None, self.staff, "Es otra persona sin cuenta"), 0)
        self.i1.refresh_from_db()
        self.assertIsNone(self.i1.usuario_2)
        self.assertEqual(RevisionIdentidad.objects.get().estado, RevisionIdentidad.ESTADO_RESUELTA)

    def test_el_titular_tiene_que_ser_una_de_las_cuentas_involucradas(self):
        ajena = crear_usuario(dni="12345678")
        with self.assertRaises(ValueError):
            resolver_revision(self.caso, ajena, self.staff, "x")
        self.caso.refresh_from_db()
        self.assertEqual(self.caso.estado, RevisionIdentidad.ESTADO_ABIERTA)

    def test_no_se_resuelve_dos_veces(self):
        resolver_revision(self.caso, self.a, self.staff, "x")
        with self.assertRaises(ValueError):
            resolver_revision(self.caso, self.b, self.staff, "x")

    def test_si_los_dni_siguen_duplicados_un_nuevo_uso_abre_otro_caso(self):
        resolver_revision(self.caso, self.a, self.staff, "x")
        nueva = crear_inscripcion(crear_categoria(), dni_2="27555000")
        vincular_companero(nueva)
        self.assertEqual(RevisionIdentidad.objects.filter(estado="abierta").count(), 1)
        self.assertEqual(RevisionIdentidad.objects.count(), 2)
        nueva.refresh_from_db()
        self.assertIsNone(nueva.usuario_2)  # sigue sin vincularse a ciegas

    def test_la_base_impide_dos_casos_abiertos_para_el_mismo_dni(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            RevisionIdentidad.objects.create(dni="27555000")


class RevisionIdentidadAdminTests(TestCase):
    def setUp(self):
        self.a = crear_usuario(dni="27.555.000", categoria_oficial=5)
        self.b = crear_usuario(dni="27555000", categoria_oficial=8)
        self.insc = crear_inscripcion(crear_categoria(), dni_2="27555000", categoria_oficial_2=7)
        self.caso = registrar_revision_dni("27555000", [self.a, self.b], [self.insc])
        self.admin = get_user_model().objects.create_superuser("root", "r@r.com", "x")
        self.c = Client()
        self.c.force_login(self.admin)
        self.url = f"/admin/torneos/revisionidentidad/{self.caso.pk}/change/"

    def test_se_listan_y_se_abren_los_casos_pero_no_se_crean_a_mano(self):
        self.assertContains(self.c.get("/admin/torneos/revisionidentidad/"), "27555000")
        self.assertContains(self.c.get(self.url), "27555000")
        self.assertEqual(self.c.get("/admin/torneos/revisionidentidad/add/").status_code, 403)

    def test_resolver_desde_el_admin_vincula_y_deja_constancia(self):
        r = self.c.post(self.url, {"estado": "resuelta", "titular_verificado": self.a.pk,
                                   "nota_resolucion": "Verificado con DNI físico en sede", "_save": "Guardar"})
        self.assertEqual(r.status_code, 302)
        self.caso.refresh_from_db(); self.insc.refresh_from_db()
        self.assertEqual(self.caso.estado, "resuelta")
        self.assertEqual(self.caso.resuelta_por, self.admin)
        self.assertEqual(self.insc.usuario_2, self.a)
        self.assertEqual(self.insc.categoria_oficial_2, 7)

    def test_no_se_puede_resolver_sin_constancia_ni_con_una_cuenta_ajena(self):
        ajena = crear_usuario(dni="12345678")
        for datos in (
            {"estado": "resuelta", "titular_verificado": self.a.pk, "nota_resolucion": ""},
            {"estado": "resuelta", "titular_verificado": ajena.pk, "nota_resolucion": "x"},
        ):
            r = self.c.post(self.url, {**datos, "_save": "Guardar"})
            self.assertEqual(r.status_code, 200)  # vuelve con el error
        self.caso.refresh_from_db()
        self.assertEqual(self.caso.estado, "abierta")

    def test_un_caso_resuelto_ya_no_se_edita(self):
        resolver_revision(self.caso, self.a, self.admin, "ok")
        self.assertContains(self.c.get(self.url), "27555000")
        self.c.post(self.url, {"estado": "abierta", "nota_resolucion": "cambio", "_save": "Guardar"})
        self.caso.refresh_from_db()
        self.assertEqual(self.caso.estado, "resuelta")
