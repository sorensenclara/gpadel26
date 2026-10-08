import random
import string

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import NIVEL_LABELS


def default_codigo():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class Torneo(models.Model):
    FORMATO_GRUPOS_ELIMINACION = "grupos_eliminacion"
    FORMATO_SOLO_ELIMINACION = "solo_eliminacion"
    FORMATO_CHOICES = [
        (FORMATO_GRUPOS_ELIMINACION, "Fase de grupos (zonas) + eliminación directa"),
        (FORMATO_SOLO_ELIMINACION, "Solo eliminación directa"),
    ]

    MODALIDAD_POR_CATEGORIA = "por_categoria"
    MODALIDAD_POR_SUMATORIA = "por_sumatoria"
    MODALIDAD_CATEGORIA_CHOICES = [
        (MODALIDAD_POR_CATEGORIA, "Por categoría (según el jugador de mayor nivel)"),
        (MODALIDAD_POR_SUMATORIA, "Por sumatoria (suma de las categorías de la pareja)"),
    ]

    ESTADO_BORRADOR = "borrador"
    ESTADO_PUBLICADO = "publicado"
    ESTADO_CERRADO = "cerrado"
    ESTADO_CHOICES = [
        (ESTADO_BORRADOR, "Borrador (no visible para jugadores)"),
        (ESTADO_PUBLICADO, "Publicado (abierto a inscripción)"),
        (ESTADO_CERRADO, "Cerrado"),
    ]

    codigo = models.SlugField(max_length=20, unique=True, default=default_codigo)
    organizador = models.ForeignKey(
        "accounts.Organizador", on_delete=models.CASCADE, related_name="torneos"
    )
    nombre = models.CharField(max_length=150)
    sede = models.CharField(max_length=150, blank=True, help_text="Club/cancha y ciudad.")
    ciudad = models.CharField(max_length=100, blank=True)
    latitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    longitud = models.DecimalField(max_digits=10, decimal_places=7, null=True, blank=True)
    fecha_inicio = models.DateField()
    fecha_fin = models.DateField()
    fecha_limite_inscripcion = models.DateField()
    precio_inscripcion = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    formato = models.CharField(
        max_length=30, choices=FORMATO_CHOICES, default=FORMATO_GRUPOS_ELIMINACION
    )
    modalidad_categoria = models.CharField(
        max_length=20, choices=MODALIDAD_CATEGORIA_CHOICES, default=MODALIDAD_POR_CATEGORIA,
        help_text="Cómo se valida la categoría de la pareja al inscribirse.",
    )
    categorias_permitir_subir = models.PositiveSmallIntegerField(
        default=0,
        help_text="Solo para modalidad 'Por categoría': cuántas categorías por encima de la "
        "que le corresponde a la pareja se le permite jugar (0 = solo la suya).",
    )
    sumatoria_valor = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Solo para modalidad 'Por sumatoria': el torneo es exclusivamente "
        "'SUMA <valor>' — la suma de categorías de la pareja tiene que ser ≥ este valor. "
        "Reemplaza a la lista de categorías.",
    )
    permite_multiples_categorias = models.BooleanField(
        default=False,
        help_text="Si está activado, un jugador puede inscribirse en más de una categoría del torneo: "
        "solo en categorías MÁS ALTAS que la anterior y siempre con una pareja distinta.",
    )
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=ESTADO_BORRADOR)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_inicio"]

    def __str__(self):
        return self.nombre

    def validar_categoria_pareja(self, categoria, nivel_1, nivel_2):
        """
        Devuelve (True, None) si la pareja (niveles oficiales 1..8, 1=mejor)
        puede inscribirse en `categoria` según la modalidad del torneo, o
        (False, mensaje) con el motivo si no puede.
        """
        if nivel_1 is None or nivel_2 is None:
            return False, "Falta indicar la categoría oficial de uno de los dos jugadores."

        if self.modalidad_categoria == self.MODALIDAD_POR_SUMATORIA:
            suma = nivel_1 + nivel_2
            minimo = categoria.sumatoria_minima
            if minimo is None:
                return True, None
            if suma < minimo:
                return False, (
                    f"La suma de categorías de la pareja ({suma}) es menor al mínimo "
                    f"requerido para {categoria.nombre} (mínimo {minimo})."
                )
            return True, None

        # Por categoría: la pareja se ubica en la MEJOR (número más chico) de
        # los dos jugadores, y puede subir hasta `categorias_permitir_subir`
        # categorías por encima (número todavía más chico) — nunca bajar.
        base = min(nivel_1, nivel_2)
        tope_superior = max(base - self.categorias_permitir_subir, 1)
        nivel_categoria = categoria.nivel_numerico
        if nivel_categoria > base:
            return False, (
                f"La pareja no puede inscribirse en una categoría inferior a la que le "
                f"corresponde ({NIVEL_LABELS[base]})."
            )
        if nivel_categoria < tope_superior:
            return False, (
                f"Esta pareja puede inscribirse como máximo {self.categorias_permitir_subir} "
                f"categoría(s) por encima de la suya ({NIVEL_LABELS[base]}): hasta "
                f"{NIVEL_LABELS[tope_superior]}."
            )
        return True, None

    @property
    def esta_publicado(self):
        return self.estado == self.ESTADO_PUBLICADO

    @property
    def estado_visual(self):
        """
        Estado mostrado en 'Mis torneos', calculado por fecha además del
        estado guardado: un torneo publicado pasa solo de "Inscripción
        abierta" a "En juego" a "Finalizado" a medida que pasan las fechas,
        sin que el Organizador tenga que tocar nada.
        """
        if self.estado == self.ESTADO_BORRADOR:
            return "borrador"
        hoy = timezone.localdate()
        if self.estado == self.ESTADO_CERRADO or hoy > self.fecha_fin:
            return "finalizado"
        if hoy >= self.fecha_inicio:
            return "en_juego"
        return "inscripcion_abierta"

    ESTADO_VISUAL_LABELS = {
        "borrador": "Borrador · No visible para jugadores",
        "inscripcion_abierta": "Publicado · Inscripción abierta",
        "en_juego": "Publicado · En juego",
        "finalizado": "Finalizado",
    }

    @property
    def estado_visual_label(self):
        return self.ESTADO_VISUAL_LABELS[self.estado_visual]

    ESTADO_BADGE_CORTO = {
        "borrador": "Borrador",
        "inscripcion_abierta": "Publicado",
        "en_juego": "Publicado",
        "finalizado": "Finalizado",
    }
    ESTADO_SUBTITULO = {
        "borrador": "No visible hasta que lo publiques.",
        "inscripcion_abierta": "Visible para los jugadores, recibiendo inscripciones.",
        "en_juego": "El torneo ya está en juego.",
        "finalizado": "El torneo ya terminó.",
    }

    @property
    def estado_badge_corto(self):
        return self.ESTADO_BADGE_CORTO[self.estado_visual]

    @property
    def estado_subtitulo(self):
        return self.ESTADO_SUBTITULO[self.estado_visual]

    @property
    def categorias_count(self):
        return self.categorias.count()

    @property
    def parejas_inscriptas_count(self):
        return Inscripcion.objects.filter(
            torneo=self, estado=Inscripcion.ESTADO_CONFIRMADA
        ).count()


class Categoria(models.Model):
    NOMBRE_CHOICES = [
        ("1ra LIBRE", "1ra LIBRE"),
        ("2da LIBRE", "2da LIBRE"),
        ("3ra LIBRE", "3ra LIBRE"),
        ("4ta LIBRE", "4ta LIBRE"),
        ("5ta LIBRE", "5ta LIBRE"),
        ("6ta LIBRE", "6ta LIBRE"),
        ("7ma LIBRE", "7ma LIBRE"),
        ("8va LIBRE", "8va LIBRE"),
        ("1ra DAMAS", "1ra DAMAS"),
        ("2da DAMAS", "2da DAMAS"),
        ("3ra DAMAS", "3ra DAMAS"),
        ("4ta DAMAS", "4ta DAMAS"),
        ("5ta DAMAS", "5ta DAMAS"),
        ("6ta DAMAS", "6ta DAMAS"),
        ("7ma DAMAS", "7ma DAMAS"),
        ("8va DAMAS", "8va DAMAS"),
        ("1ra MIXTO", "1ra MIXTO"),
        ("2da MIXTO", "2da MIXTO"),
        ("3ra MIXTO", "3ra MIXTO"),
        ("4ta MIXTO", "4ta MIXTO"),
    ]

    torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name="categorias")
    nombre = models.CharField(
        max_length=50,
        choices=NOMBRE_CHOICES,
        help_text="Categoría estándar (evita variantes como '5ta LIBRE' vs '5ta libre').",
    )
    cupo_minimo = models.PositiveIntegerField(
        default=3, help_text="Mínimo de parejas inscriptas para habilitar la categoría."
    )
    cupo_maximo = models.PositiveIntegerField(null=True, blank=True)
    orden = models.PositiveIntegerField(default=0)
    sumatoria_minima = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Solo para modalidad 'Por sumatoria': suma mínima de las categorías "
        "oficiales de la pareja (1ra=1 .. 8va=8) para poder inscribirse acá.",
    )

    # ---- Estado de la competencia (una Categoria ES una competencia) ----
    # Tres ejes independientes, sin estados mezclados:
    #   1) etapa: dónde está la competencia en su vida (avance general).
    #   2) grupos_publicacion: si la fase de grupos es visible para los jugadores.
    #   3) llave_publicacion: si la llave es visible para los jugadores.
    # El "cierre de inscripción" NO se guarda: se deriva de
    # Torneo.fecha_limite_inscripcion (ver Torneo.estado_visual).
    ETAPA_INSCRIPCION = "inscripcion"
    ETAPA_ORGANIZACION_GRUPOS = "organizacion_grupos"
    ETAPA_JUEGO_GRUPOS = "juego_grupos"
    ETAPA_GRUPOS_FINALIZADOS = "grupos_finalizados"
    ETAPA_JUEGO_LLAVE = "juego_llave"
    ETAPA_FINALIZADA = "finalizada"
    ETAPA_CHOICES = [
        (ETAPA_INSCRIPCION, "Inscripción"),
        (ETAPA_ORGANIZACION_GRUPOS, "Organizando fase de grupos"),
        (ETAPA_JUEGO_GRUPOS, "Jugando fase de grupos"),
        (ETAPA_GRUPOS_FINALIZADOS, "Fase de grupos finalizada (falta llave)"),
        (ETAPA_JUEGO_LLAVE, "Jugando llave"),
        (ETAPA_FINALIZADA, "Competencia finalizada"),
    ]
    # Orden de avance, para validar combinaciones imposibles.
    ETAPA_ORDEN = {clave: i for i, (clave, _) in enumerate(ETAPA_CHOICES)}

    PUBLICACION_SIN_DEFINIR = "sin_definir"  # todavía no hay propuesta elegida / llave generada
    PUBLICACION_BORRADOR = "borrador"        # existe, solo la ve el organizador
    PUBLICACION_PUBLICADA = "publicada"      # visible para todos los jugadores
    PUBLICACION_CHOICES = [
        (PUBLICACION_SIN_DEFINIR, "Sin definir"),
        (PUBLICACION_BORRADOR, "Borrador"),
        (PUBLICACION_PUBLICADA, "Publicada"),
    ]

    etapa = models.CharField(
        max_length=25, choices=ETAPA_CHOICES, default=ETAPA_INSCRIPCION,
        help_text="Avance general de esta competencia (independiente de las demás categorías).",
    )
    grupos_publicacion = models.CharField(
        max_length=12, choices=PUBLICACION_CHOICES, default=PUBLICACION_SIN_DEFINIR,
        help_text="Visibilidad de la fase de grupos para los jugadores.",
    )
    grupos_publicados_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    grupos_publicados_en = models.DateTimeField(null=True, blank=True)
    llave_publicacion = models.CharField(
        max_length=12, choices=PUBLICACION_CHOICES, default=PUBLICACION_SIN_DEFINIR,
        help_text="Visibilidad de la llave eliminatoria para los jugadores.",
    )
    llave_publicada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    llave_publicada_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return f"{self.nombre} ({self.torneo.nombre})"

    def clean(self):
        """Rechaza combinaciones de estado imposibles (ej.: llave publicada
        mientras todavía se están organizando los grupos)."""
        from django.core.exceptions import ValidationError

        orden = self.ETAPA_ORDEN.get(self.etapa, 0)
        errores = {}
        if self.grupos_publicacion != self.PUBLICACION_SIN_DEFINIR and orden < self.ETAPA_ORDEN[
            self.ETAPA_ORGANIZACION_GRUPOS
        ]:
            errores["grupos_publicacion"] = (
                "No puede haber propuesta de grupos elegida mientras la competencia está en inscripción."
            )
        if self.grupos_publicacion == self.PUBLICACION_PUBLICADA and orden < self.ETAPA_ORDEN[
            self.ETAPA_JUEGO_GRUPOS
        ]:
            errores["grupos_publicacion"] = (
                "La fase de grupos solo puede estar publicada una vez que se juega o terminó."
            )
        if self.llave_publicacion != self.PUBLICACION_SIN_DEFINIR and orden < self.ETAPA_ORDEN[
            self.ETAPA_GRUPOS_FINALIZADOS
        ]:
            errores["llave_publicacion"] = (
                "La llave solo existe cuando terminó la fase de grupos."
            )
        if self.llave_publicacion == self.PUBLICACION_PUBLICADA and self.grupos_publicacion != (
            self.PUBLICACION_PUBLICADA
        ):
            errores["llave_publicacion"] = (
                "No se puede publicar la llave si la fase de grupos no fue publicada."
            )
        if errores:
            raise ValidationError(errores)

    @property
    def nivel_numerico(self):
        """'6ta LIBRE' -> 6. Todos los sufijos de NOMBRE_CHOICES (ra/da/ta/ma/va)
        tienen 2 letras, así que alcanza con sacar el primer token menos esas 2."""
        primer_token = self.nombre.split()[0]
        return int(primer_token[:-2])

    @property
    def inscriptos_count(self):
        return self.inscripciones.filter(estado=Inscripcion.ESTADO_CONFIRMADA).count()

    @property
    def habilitada(self):
        return self.inscriptos_count >= self.cupo_minimo

    @property
    def color_css(self):
        if "DAMAS" in self.nombre:
            return "#ec4899"
        if "MIXTO" in self.nombre:
            return "#22c55e"
        return "#3b82f6"  # LIBRE


class Inscripcion(models.Model):
    """
    Inscripción de una PAREJA a una categoría de un torneo. Reemplaza el
    formulario de Google Forms que usaban antes.
    """

    PAGO_MERCADOPAGO = "mercadopago"
    PAGO_TRANSFERENCIA = "transferencia"
    PAGO_EFECTIVO = "efectivo"
    PAGO_CHOICES = [
        (PAGO_MERCADOPAGO, "Mercado Pago"),
        (PAGO_TRANSFERENCIA, "Transferencia"),
        (PAGO_EFECTIVO, "Efectivo (en la cancha)"),
    ]

    ESTADO_PENDIENTE = "pendiente"
    ESTADO_CONFIRMADA = "confirmada"
    ESTADO_RECHAZADA = "rechazada"
    ESTADO_CANCELADA = "cancelada"
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, "Pendiente de pago/habilitación"),
        (ESTADO_CONFIRMADA, "Confirmada"),
        (ESTADO_RECHAZADA, "Rechazada"),
        (ESTADO_CANCELADA, "Cancelada"),
    ]
    # "Vigente" = ocupa lugar: pendiente o confirmada. Rechazada (por el organizador) y cancelada
    # (se dio de baja) NO cuentan para duplicados, cupos ni para el estado INSCRIPTO: la pareja
    # queda libre para volver a inscribirse.
    ESTADOS_NO_VIGENTES = (ESTADO_RECHAZADA, ESTADO_CANCELADA)

    torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name="inscripciones")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inscripciones",
        help_text="Quién hizo la inscripción (Jugador 1). Nulo en inscripciones muy viejas, "
        "de antes de exigir login para inscribirse.",
    )
    usuario_2 = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inscripciones_como_companero",
        help_text="Cuenta del compañero (Jugador 2), si tiene una con ese DNI. Es solo un "
        "vínculo: NO modifica las categorías declaradas en esta inscripción, que quedan "
        "como dato histórico aunque el jugador cambie después su categoría oficial.",
    )
    # Identidad deportiva (persona) de cada integrante. Es a lo que quedan asociados el
    # historial, los resultados y los rankings, tenga o no cuenta. Se completan solos
    # (ver torneos.servicios.asignar_personas) y pueden quedar vacíos:
    #  - jugador 1: se toma SIEMPRE de su cuenta, nunca del DNI tipeado; queda vacío
    #    mientras su cuenta tiene un reclamo de identidad pendiente (se completa al resolverlo);
    #  - jugador 2: se busca por DNI; queda vacío ante un DNI ambiguo o un nombre que no coincide.
    # No modifican NINGÚN dato declarado de la inscripción (DNI, nombres, categorías).
    persona_1 = models.ForeignKey(
        "accounts.Identidad", null=True, blank=True, on_delete=models.PROTECT,
        related_name="inscripciones_como_jugador_1",
    )
    persona_2 = models.ForeignKey(
        "accounts.Identidad", null=True, blank=True, on_delete=models.PROTECT,
        related_name="inscripciones_como_jugador_2",
    )
    categoria = models.ForeignKey(
        Categoria, on_delete=models.CASCADE, related_name="inscripciones"
    )
    categoria_real = models.CharField(
        max_length=50,
        blank=True,
        help_text="Categoría real del jugador si es distinta a la declarada.",
    )

    # Jugador 1
    nombre_1 = models.CharField("Nombre", max_length=150)
    apellido_1 = models.CharField("Apellido", max_length=100, blank=True)
    dni_1 = models.CharField("DNI", max_length=20)
    celular_1 = models.CharField("Celular", max_length=30, blank=True)
    localidad_1 = models.CharField("Localidad", max_length=100)
    categoria_oficial_1 = models.PositiveSmallIntegerField(
        "Categoría oficial", null=True, blank=True,
        help_text="1ra es la más alta. Queda guardada en el perfil del jugador.",
    )
    socio_1 = models.BooleanField("Es socio del club", default=False)
    club_socio_1 = models.CharField(
        "Club del que es socio", max_length=150, blank=True,
        help_text="Puede ser un club distinto al organizador.",
    )
    carnet_socio_1 = models.CharField("N° de carnet", max_length=50, blank=True)

    # Jugador 2
    nombre_2 = models.CharField("Nombre", max_length=150)
    apellido_2 = models.CharField("Apellido", max_length=100, blank=True)
    dni_2 = models.CharField("DNI", max_length=20)
    localidad_2 = models.CharField("Localidad", max_length=100)
    categoria_oficial_2 = models.PositiveSmallIntegerField(
        "Categoría oficial del compañero", null=True, blank=True,
    )
    socio_2 = models.BooleanField("Es socio del club", default=False)
    club_socio_2 = models.CharField(
        "Club del que es socio", max_length=150, blank=True,
        help_text="Puede ser un club distinto al organizador.",
    )
    carnet_socio_2 = models.CharField("N° de carnet", max_length=50, blank=True)

    telefono = models.CharField("Teléfono de la pareja", max_length=30)
    disponibilidad = models.CharField(
        "Días/horarios disponibles",
        max_length=255,
        blank=True,
        help_text="No excluyente: solo mejora el emparejamiento cuando es posible.",
    )

    metodo_pago = models.CharField(max_length=20, choices=PAGO_CHOICES, default=PAGO_TRANSFERENCIA)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(persona_1=models.F("persona_2")),
                name="inscripcion_personas_distintas",
            ),
        ]

    def __str__(self):
        return f"{self.nombre_completo_1} / {self.nombre_completo_2} — {self.categoria}"

    # Las inscripciones anteriores guardaban "Apellido y nombre" juntos en nombre_X (con el
    # apellido vacío): estas propiedades sirven igual para las dos formas.
    @property
    def nombre_completo_1(self):
        return f"{self.nombre_1} {self.apellido_1}".strip()

    @property
    def nombre_completo_2(self):
        return f"{self.nombre_2} {self.apellido_2}".strip()


class DisponibilidadInscripcion(models.Model):
    """
    Una franja de disponibilidad de una pareja (ej.: viernes 18:00–22:00).
    Varias por inscripción. Reemplaza, para inscripciones nuevas, al texto
    libre `Inscripcion.disponibilidad` (que se conserva intacto para lo
    histórico y para no romper pantallas que lo leen).
    """

    DIA_CHOICES = [
        (0, "Lunes"), (1, "Martes"), (2, "Miércoles"), (3, "Jueves"),
        (4, "Viernes"), (5, "Sábado"), (6, "Domingo"),
    ]
    DIA_POR_NOMBRE = {nombre.lower(): valor for valor, nombre in DIA_CHOICES}

    inscripcion = models.ForeignKey(
        Inscripcion, on_delete=models.CASCADE, related_name="disponibilidades"
    )
    dia = models.PositiveSmallIntegerField(choices=DIA_CHOICES)
    hora_desde = models.TimeField()
    hora_hasta = models.TimeField()

    class Meta:
        ordering = ["dia", "hora_desde"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hora_hasta__gt=models.F("hora_desde")),
                name="disponibilidad_hora_hasta_mayor_que_desde",
            ),
        ]

    def __str__(self):
        return f"{self.get_dia_display()} {self.hora_desde:%H:%M}–{self.hora_hasta:%H:%M}"


class RevisionIdentidad(models.Model):
    """
    Caso de identidad que NO se puede resolver automáticamente y requiere
    verificación humana. Hoy: un mismo DNI (comparado normalizado) que
    coincide con más de una cuenta. Mientras haya un caso abierto no se
    vincula nada ni se usa la categoría oficial de ninguna de esas cuentas.

    Se resuelve verificando la identidad real de la persona (ver
    torneos.servicios.resolver_revision); nunca por inferencia.
    """

    MOTIVO_DNI_AMBIGUO = "dni_ambiguo"
    MOTIVO_NOMBRE_NO_COINCIDE = "nombre_no_coincide"
    MOTIVO_VINCULO_RETROACTIVO = "vinculo_retroactivo"
    MOTIVO_CHOICES = [
        (MOTIVO_DNI_AMBIGUO, "DNI coincide con varias identidades"),
        (MOTIVO_NOMBRE_NO_COINCIDE, "DNI coincide pero el nombre no"),
        (MOTIVO_VINCULO_RETROACTIVO, "Inscripción anterior a la cuenta que usa su DNI"),
    ]

    ESTADO_ABIERTA = "abierta"
    ESTADO_RESUELTA = "resuelta"
    ESTADO_CHOICES = [(ESTADO_ABIERTA, "Abierta"), (ESTADO_RESUELTA, "Resuelta")]

    dni = models.CharField(max_length=20, db_index=True, help_text="DNI normalizado (solo dígitos).")
    motivo = models.CharField(max_length=20, choices=MOTIVO_CHOICES, default=MOTIVO_DNI_AMBIGUO)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=ESTADO_ABIERTA)
    cuentas = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="revisiones_identidad",
        help_text="Cuentas que comparten ese DNI.",
    )
    inscripciones = models.ManyToManyField(
        Inscripcion, blank=True, related_name="revisiones_identidad",
        help_text="Inscripciones cuyo compañero no se pudo identificar por este motivo.",
    )
    detectada_en = models.DateTimeField(auto_now_add=True)

    titular_verificado = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Cuenta que, tras verificar su identidad, es la titular de ese DNI. Vacío si "
        "ninguna de las cuentas corresponde.",
    )
    nota_resolucion = models.TextField(blank=True)
    resuelta_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resuelta_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-detectada_en"]
        verbose_name = "revisión de identidad"
        verbose_name_plural = "revisiones de identidad"
        constraints = [
            # A lo sumo un caso abierto por DNI y motivo: no se duplican.
            models.UniqueConstraint(
                fields=["dni", "motivo"],
                condition=models.Q(estado="abierta"),
                name="revision_identidad_una_abierta_por_dni",
            ),
            models.CheckConstraint(
                condition=models.Q(estado="abierta", resuelta_en__isnull=True)
                | models.Q(estado="resuelta", resuelta_en__isnull=False),
                name="revision_identidad_resuelta_tiene_fecha",
            ),
        ]

    def __str__(self):
        return f"DNI {self.dni} — {self.get_motivo_display()} ({self.get_estado_display()})"
