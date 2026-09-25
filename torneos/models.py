import random
import string

from django.db import models
from django.utils import timezone


def default_codigo():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


class Torneo(models.Model):
    FORMATO_GRUPOS_ELIMINACION = "grupos_eliminacion"
    FORMATO_SOLO_ELIMINACION = "solo_eliminacion"
    FORMATO_CHOICES = [
        (FORMATO_GRUPOS_ELIMINACION, "Fase de grupos (zonas) + eliminación directa"),
        (FORMATO_SOLO_ELIMINACION, "Solo eliminación directa"),
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
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=ESTADO_BORRADOR)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["fecha_inicio"]

    def __str__(self):
        return self.nombre

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

    class Meta:
        ordering = ["orden", "id"]

    def __str__(self):
        return f"{self.nombre} ({self.torneo.nombre})"

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
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, "Pendiente de pago/habilitación"),
        (ESTADO_CONFIRMADA, "Confirmada"),
        (ESTADO_RECHAZADA, "Rechazada"),
    ]

    torneo = models.ForeignKey(Torneo, on_delete=models.CASCADE, related_name="inscripciones")
    categoria = models.ForeignKey(
        Categoria, on_delete=models.CASCADE, related_name="inscripciones"
    )
    categoria_real = models.CharField(
        max_length=50,
        blank=True,
        help_text="Categoría real del jugador si es distinta a la declarada.",
    )

    # Jugador 1
    nombre_1 = models.CharField("Apellido y nombre", max_length=150)
    dni_1 = models.CharField("DNI", max_length=20)
    localidad_1 = models.CharField("Localidad", max_length=100)
    socio_1 = models.BooleanField("Es socio del club", default=False)
    club_socio_1 = models.CharField(
        "Club del que es socio", max_length=150, blank=True,
        help_text="Puede ser un club distinto al organizador.",
    )
    carnet_socio_1 = models.CharField("N° de carnet", max_length=50, blank=True)

    # Jugador 2
    nombre_2 = models.CharField("Apellido y nombre", max_length=150)
    dni_2 = models.CharField("DNI", max_length=20)
    localidad_2 = models.CharField("Localidad", max_length=100)
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

    def __str__(self):
        return f"{self.nombre_1} / {self.nombre_2} — {self.categoria}"
