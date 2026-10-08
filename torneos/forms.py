from django import forms
from django.forms import formset_factory, modelformset_factory

from accounts.models import NIVEL_CHOICES, NIVEL_LABELS, Identidad

import logging

from .models import Categoria, Inscripcion, Torneo
from .participaciones import Participante, categorias_disponibles, inscripciones_de, validar_participacion
from .servicios import (
    Companero,
    buscar_companero,
    categoria_minima_declarada,
    normalizar_dni,
    puede_subir_categoria,
    registrar_disponibilidad_desde_texto,
    vincular_companero,
)

logger = logging.getLogger(__name__)


def _completar_datos_derivados(inscripcion):
    """Vínculo del compañero por DNI y disponibilidad estructurada.
    Es información complementaria: si algo falla, la inscripción ya
    quedó guardada y no debe romperse por esto."""
    try:
        vincular_companero(inscripcion)
        registrar_disponibilidad_desde_texto(inscripcion)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudieron completar los datos derivados de la inscripción %s", inscripcion.pk)


class TorneoForm(forms.ModelForm):
    """
    Paso 1 y 3 de la especificación: formato + datos generales del torneo
    (nombre, sede, fechas, precio, fecha límite de inscripción).
    """

    class Meta:
        model = Torneo
        fields = [
            "nombre",
            "sede",
            "ciudad",
            "latitud",
            "longitud",
            "formato",
            "modalidad_categoria",
            "categorias_permitir_subir",
            "permite_multiples_categorias",
            "sumatoria_valor",
            "fecha_inicio",
            "fecha_fin",
            "fecha_limite_inscripcion",
            "precio_inscripcion",
        ]
        widgets = {
            "fecha_inicio": forms.DateInput(attrs={"type": "date"}),
            "fecha_fin": forms.DateInput(attrs={"type": "date"}),
            "fecha_limite_inscripcion": forms.DateInput(attrs={"type": "date"}),
            "formato": forms.RadioSelect,
            "modalidad_categoria": forms.RadioSelect(attrs={"id": "gpModalidadCategoria"}),
            "categorias_permitir_subir": forms.NumberInput(attrs={"min": "0", "max": "7", "id": "gpPermitirSubir"}),
            "sumatoria_valor": forms.NumberInput(attrs={"min": "2", "max": "16", "id": "gpSumatoriaValor"}),
            "ciudad": forms.TextInput(attrs={"id": "gpCiudadInput", "autocomplete": "off"}),
            "latitud": forms.HiddenInput(attrs={"id": "gpLatInput"}),
            "longitud": forms.HiddenInput(attrs={"id": "gpLonInput"}),
            "precio_inscripcion": forms.NumberInput(attrs={"min": "0", "step": "0.01", "style": "padding-left:28px;"}),
        }

    def clean(self):
        cleaned = super().clean()
        inicio = cleaned.get("fecha_inicio")
        fin = cleaned.get("fecha_fin")
        limite = cleaned.get("fecha_limite_inscripcion")
        if inicio and fin and fin < inicio:
            self.add_error("fecha_fin", "No puede ser anterior a la fecha de inicio.")
        if inicio and limite and limite > inicio:
            self.add_error(
                "fecha_limite_inscripcion",
                "Tiene que ser antes (o el mismo día) del inicio del torneo.",
            )
        if cleaned.get("modalidad_categoria") == Torneo.MODALIDAD_POR_SUMATORIA and not cleaned.get(
            "sumatoria_valor"
        ):
            self.add_error("sumatoria_valor", "Indicá el valor de suma mínima para este torneo.")
        return cleaned


class CategoriaForm(forms.ModelForm):
    """Paso 2: cada categoría posible + el mínimo de parejas para habilitarla.
    Solo se usa en modalidad "Por categoría" — en "Por sumatoria" el torneo
    entero es una única categoría ("SUMA <n>"), armada automáticamente a
    partir de `Torneo.sumatoria_valor` (ver torneos.views)."""

    class Meta:
        model = Categoria
        fields = ["nombre", "cupo_minimo"]


# min_num=1 ya garantiza que se muestre 1 fila vacía; extra=1 la duplicaba a 2.
CategoriaFormSet = formset_factory(CategoriaForm, extra=0, min_num=1, validate_min=True, max_num=10)

# Para editar un torneo existente: parte de las categorías ya creadas (queryset),
# permite borrarlas de verdad (can_delete) y agregar una nueva (extra=1).
CategoriaEditFormSet = modelformset_factory(
    Categoria, form=CategoriaForm, extra=1, can_delete=True, max_num=15
)


class InscripcionForm(forms.ModelForm):
    """
    Formulario de inscripción de una PAREJA a un torneo — reemplaza el
    Google Form. Los mismos campos que ya usan hoy en la planilla.
    """

    categoria_real = forms.ChoiceField(
        choices=[("", "— Igual a la declarada —")] + Categoria.NOMBRE_CHOICES,
        required=False,
        label="Categoría real",
    )
    # Opción vacía a propósito: sin ella quedaría preseleccionada 1ra, y como
    # la categoría solo se puede subir, un descuido sería irreversible.
    # Compañero elegido de la lista de GPADEL (por identificador: la búsqueda no revela DNI).
    companero_id = forms.IntegerField(required=False, widget=forms.HiddenInput(attrs={"id": "gpCompaneroId"}))
    categoria_oficial_1 = forms.TypedChoiceField(
        choices=[("", "Seleccioná tu categoría")] + NIVEL_CHOICES, coerce=int, label="Mi categoría actual",
        widget=forms.Select(attrs={"id": "gpCategoriaOficial1"}),
    )
    # No es obligatorio a nivel de campo porque, si el compañero tiene cuenta con
    # categoría registrada, se toma de su perfil (ver clean()) y no de lo que
    # llegue del navegador.
    categoria_oficial_2 = forms.TypedChoiceField(
        choices=[("", "Seleccioná la categoría")] + NIVEL_CHOICES, coerce=int, empty_value=None, required=False,
        label="Categoría oficial del compañero",
        widget=forms.Select(attrs={"id": "gpCategoriaOficial2"}),
    )

    class Meta:
        model = Inscripcion
        fields = [
            "categoria",
            "categoria_real",
            "nombre_1",
            "apellido_1",
            "dni_1",
            "celular_1",
            "localidad_1",
            "categoria_oficial_1",
            "socio_1",
            "club_socio_1",
            "carnet_socio_1",
            "nombre_2",
            "apellido_2",
            "dni_2",
            "localidad_2",
            "categoria_oficial_2",
            "socio_2",
            "club_socio_2",
            "carnet_socio_2",
            "telefono",
            "disponibilidad",
            "metodo_pago",
        ]
        labels = {"nombre_1": "Nombre", "apellido_1": "Apellido", "nombre_2": "Nombre", "apellido_2": "Apellido"}
        widgets = {
            "metodo_pago": forms.RadioSelect,
            "disponibilidad": forms.HiddenInput(attrs={"id": "gpDispHidden"}),
        }

    def __init__(self, *args, torneo=None, usuario=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.torneo = torneo
        self.usuario = usuario
        self.companero = Companero()
        if torneo is not None:
            self.fields["categoria"].queryset = torneo.categorias.all()
            if not self.is_bound and usuario is not None and usuario.is_authenticated:
                # Al mostrar el formulario, solo se ofrecen las categorías en las que todavía puede
                # inscribirse. (Al enviar se valida contra todas, con un mensaje claro.)
                previas = list(inscripciones_de(usuario).filter(torneo=torneo).select_related("categoria"))
                if previas:
                    ids = [c.pk for c in categorias_disponibles(torneo, previas)]
                    self.fields["categoria"].queryset = torneo.categorias.filter(pk__in=ids)

        self.fields["celular_1"].required = True
        self.fields["apellido_1"].required = True
        # Del compañero: si ya está en la plataforma con su nombre, los completa el sistema (clean()).
        for campo in ("nombre_2", "apellido_2", "localidad_2", "dni_2"):
            self.fields[campo].required = False
        self.companero_elegido = None  # datos del sistema del compañero elegido (para mostrarlo)

        # Categoría oficial ya guardada en el perfil (si la tiene): se
        # precarga y solo se permite mejorarla (bajar el número), nunca
        # empeorarla. Si todavía no tiene ninguna cargada, la pide ahora.
        self.categoria_oficial_actual = None
        self.categoria_piso = None  # mejor categoría ya declarada, si todavía no tiene oficial
        jugador = getattr(usuario, "jugador", None) if usuario is not None else None
        if jugador is not None and jugador.categoria_oficial:
            self.categoria_oficial_actual = jugador.categoria_oficial
            if not self.is_bound:
                self.fields["categoria_oficial_1"].initial = jugador.categoria_oficial
        elif usuario is not None and usuario.is_authenticated:
            # Sin categoría oficial pero con declaraciones confirmadas (por un
            # tercero como compañero, o propias de antes de tener perfil de
            # Jugador): no puede registrar una inferior. Se sugiere confirmarla.
            self.categoria_piso = categoria_minima_declarada(usuario)
            if self.categoria_piso is not None and not self.is_bound:
                self.fields["categoria_oficial_1"].initial = self.categoria_piso

        # Si está logueado, precargamos sus propios datos como Jugador 1 —
        # así solo tiene que completar los datos de su compañero. Si están
        # completos (nombre + DNI + celular), el template los muestra como
        # resumen de solo lectura en vez de inputs editables.
        self.datos_cuenta_completos = False
        if usuario is not None and usuario.is_authenticated and not self.is_bound:
            nombre_completo = usuario.get_full_name().strip()
            if usuario.first_name and usuario.last_name:
                self.fields["nombre_1"].initial = usuario.first_name
                self.fields["apellido_1"].initial = usuario.last_name
            elif nombre_completo:
                self.fields["nombre_1"].initial = nombre_completo
            identidad = getattr(usuario, "identidad", None)
            if identidad:
                self.fields["dni_1"].initial = identidad.dni
                if identidad.localidad:
                    self.fields["localidad_1"].initial = identidad.localidad
            celular = getattr(getattr(usuario, "jugador", None), "celular", None) or getattr(
                getattr(usuario, "organizador", None), "celular", None
            )
            if celular:
                self.fields["celular_1"].initial = celular
                self.fields["telefono"].initial = celular

            self.datos_cuenta_completos = bool(
                usuario.first_name and usuario.last_name and identidad and identidad.localidad and celular
            )

    @property
    def categoria_piso_label(self):
        return NIVEL_LABELS.get(self.categoria_piso, "")

    @property
    def categoria_limite(self):
        """Categoría por debajo de la cual (número mayor) no se puede elegir:
        la oficial si ya la tiene; si no, el piso declarado; si no, ninguna."""
        if self.categoria_oficial_actual is not None:
            return self.categoria_oficial_actual
        return self.categoria_piso

    def clean(self):
        cleaned = super().clean()

        # Jugador 1 (quien inscribe): su categoría oficial es del perfil y solo
        # puede mejorarla (número más chico), nunca bajarla.
        nueva_1 = cleaned.get("categoria_oficial_1")
        if nueva_1 is not None and not puede_subir_categoria(self.categoria_limite, nueva_1):
            if self.categoria_oficial_actual is not None:
                mensaje = (
                    f"Tu categoría oficial actual es {NIVEL_LABELS[self.categoria_oficial_actual]}. "
                    "Podés mejorarla, pero no bajarla manualmente."
                )
            else:
                mensaje = (
                    f"Ya figurás declarado como {NIVEL_LABELS[self.categoria_piso]} en torneos anteriores. "
                    "Podés confirmarla o elegir una superior, no una inferior."
                )
            self.add_error("categoria_oficial_1", mensaje)

        # Jugador 2 (compañero): la categoría la decide su perfil, no quien inscribe.
        #  - con cuenta y categoría registrada -> se usa SIEMPRE esa (se ignora lo enviado);
        #  - con cuenta sin categoría, sin cuenta, o DNI ambiguo -> se usa la declarada acá,
        #    solo para esta inscripción (NO pasa a ser su categoría oficial: la confirma el titular).
        # Compañero elegido de la lista: se usa su identidad del SISTEMA (id), no lo tipeado. Tiene que ser
        # una cuenta única y no ambigua, con nombre completo, y distinta de quien inscribe.
        elegido = cleaned.get("companero_id")
        if elegido:
            identidad = (
                Identidad.objects.select_related("usuario")
                .filter(pk=elegido, usuario__isnull=False, dni_en_revision=False)
                .first()
            )
            valido = (
                identidad is not None
                and identidad.usuario.first_name and identidad.usuario.last_name
                and Identidad.objects.filter(dni_normalizado=identidad.dni_normalizado).count() == 1
                and not (self.usuario is not None and identidad.usuario_id == getattr(self.usuario, "pk", None))
            )
            if not valido:
                self.add_error("companero_id", "Elegí un compañero válido de la lista.")
                return cleaned
            cleaned["dni_2"] = identidad.dni
            self._errors.pop("dni_2", None)
            jugador_elegido = getattr(identidad.usuario, "jugador", None)
            self.companero_elegido = {
                "id": identidad.pk, "nombre": identidad.usuario.first_name, "apellido": identidad.usuario.last_name,
                "localidad": identidad.localidad,
                "categoria": getattr(jugador_elegido, "categoria_oficial", None),
            }
        elif not (cleaned.get("dni_2") or "").strip() and "dni_2" not in self._errors:
            self.add_error("dni_2", "Elegí a tu compañero de la lista o indicá su DNI.")

        dni_2 = cleaned.get("dni_2") or ""
        self.companero = buscar_companero(dni_2)
        propio = self.usuario is not None and self.usuario.is_authenticated
        if (propio and self.companero.usuario == self.usuario) or (
            normalizar_dni(dni_2) and normalizar_dni(dni_2) == normalizar_dni(cleaned.get("dni_1"))
        ):
            self.add_error("dni_2", "El compañero tiene que ser otra persona.")
            return cleaned

        # Compañero que ya está en la plataforma con su nombre: se usan los datos del SISTEMA
        # (no los enviados por el navegador). No hay nada más que completar.
        c = self.companero
        if c.tiene_cuenta and c.usuario.first_name and c.usuario.last_name:
            cleaned["nombre_2"], cleaned["apellido_2"] = c.usuario.first_name, c.usuario.last_name
            self._errors.pop("nombre_2", None)
            self._errors.pop("apellido_2", None)
            if c.persona is not None and c.persona.localidad:
                cleaned["localidad_2"] = c.persona.localidad
                self._errors.pop("localidad_2", None)
        for campo, mensaje in (
            ("nombre_2", "Indicá el nombre del compañero."),
            ("apellido_2", "Indicá el apellido del compañero."),
            ("localidad_2", "Indicá la localidad del compañero."),
        ):
            if not (cleaned.get(campo) or "").strip() and campo not in self._errors:
                self.add_error(campo, mensaje)

        if self.companero.categoria_bloqueada:
            self._errors.pop("categoria_oficial_2", None)
            cleaned["categoria_oficial_2"] = self.companero.categoria
        elif cleaned.get("categoria_oficial_2") is None and "categoria_oficial_2" not in self._errors:
            self.add_error("categoria_oficial_2", "Indicá la categoría oficial del compañero.")
        elif (
            self.companero.categoria_minima is not None
            and cleaned.get("categoria_oficial_2") is not None
            and not puede_subir_categoria(self.companero.categoria_minima, cleaned["categoria_oficial_2"])
        ):
            # Tiene cuenta pero todavía sin categoría oficial: lo que declare un
            # tercero nunca puede ser inferior a lo ya declarado y validado antes.
            self.add_error(
                "categoria_oficial_2",
                f"Para esta persona ya figura declarada {NIVEL_LABELS[self.companero.categoria_minima]}. "
                "No se puede declarar una categoría inferior.",
            )
        nueva_2 = cleaned.get("categoria_oficial_2")

        categoria = cleaned.get("categoria")
        if self.torneo is not None and categoria is not None and nueva_1 and nueva_2:
            ok, error = self.torneo.validar_categoria_pareja(categoria, nueva_1, nueva_2)
            if not ok:
                self.add_error("categoria", error)

        # Participación: misma categoría nunca; otra solo si el organizador lo habilitó, más alta y con
        # otra pareja. Se valida para los DOS integrantes (también las inscripciones que les hicieron).
        if self.torneo is not None and categoria is not None:
            for mensaje in validar_participacion(self.torneo, categoria, self._participantes(cleaned)):
                self.add_error("categoria", mensaje)

        return cleaned

    def _participantes(self, cleaned):
        """Los dos integrantes de la pareja como los ve validar_participacion (cuenta, identidad, DNI)."""
        yo_id = yo_persona = None
        dnis_yo = {normalizar_dni(cleaned.get("dni_1"))}
        es_usuario = self.usuario is not None and self.usuario.is_authenticated
        if es_usuario:
            yo_id = self.usuario.pk
            identidad = getattr(self.usuario, "identidad", None)
            if identidad is not None:
                yo_persona = identidad.pk
                dnis_yo.add(normalizar_dni(identidad.dni))
        c = self.companero
        nombre_1 = f"{cleaned.get('nombre_1', '')} {cleaned.get('apellido_1', '')}".strip()
        nombre_2 = f"{cleaned.get('nombre_2', '')} {cleaned.get('apellido_2', '')}".strip()
        return [
            Participante(nombre_1, yo_id, yo_persona, frozenset(d for d in dnis_yo if d), es_el_usuario=es_usuario),
            Participante(
                nombre_2, c.usuario.pk if c.usuario else None, c.persona.pk if c.persona else None,
                frozenset(d for d in (normalizar_dni(cleaned.get("dni_2")),) if d),
            ),
        ]

    def save(self, torneo, commit=True):
        inscripcion = super().save(commit=False)
        inscripcion.torneo = torneo
        if self.usuario is not None and self.usuario.is_authenticated:
            inscripcion.usuario = self.usuario
        # Efectivo no confirma solo: lo tiene que habilitar el organizador
        # cuando reciba el pago. Mercado Pago/transferencia quedan
        # "pendiente" también por ahora (no hay pasarela automática todavía),
        # y el organizador confirma manualmente.
        inscripcion.estado = Inscripcion.ESTADO_PENDIENTE
        if commit:
            inscripcion.save()
            _completar_datos_derivados(inscripcion)
        return inscripcion
