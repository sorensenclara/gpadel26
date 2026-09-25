from django.conf import settings
from django.db import models


class Identidad(models.Model):
    """
    Datos de la PERSONA detrás de la cuenta GPADEL, independientes de qué
    perfil(es) tenga habilitados (Jugador, Organizador, o ambos). El DNI es
    el identificador único de la persona, para evitar cuentas/jugadores
    duplicados.

    `ultimo_perfil` es lo que permite, si la cuenta tiene ambos perfiles,
    volver a caer en el mismo contexto la próxima vez que inicia sesión
    (en vez de preguntar el rol en cada ingreso).
    """

    PERFIL_JUGADOR = "jugador"
    PERFIL_ORGANIZADOR = "organizador"
    PERFIL_CHOICES = [
        (PERFIL_JUGADOR, "Jugador"),
        (PERFIL_ORGANIZADOR, "Organizador"),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="identidad",
    )
    dni = models.CharField(max_length=20, unique=True)
    ultimo_perfil = models.CharField(
        max_length=20, choices=PERFIL_CHOICES, blank=True,
        help_text="Último perfil usado (solo relevante si tiene los dos habilitados).",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Identidad"
        verbose_name_plural = "Identidades"

    def __str__(self):
        return f"{self.usuario.get_full_name() or self.usuario.username} (DNI {self.dni})"


class Organizador(models.Model):
    """
    Perfil de Dueño de cancha o Representante de Liga (roles elegidos en el
    registro). Ambos operan igual sobre marcador/torneos; tiene_cancha es lo
    único que habilita el Turnero de canchas. rol/es_liga/tiene_cancha son
    independientes entre sí para poder cruzar datos: alguien puede ser
    Dueño de cancha Y representar una Liga, o viceversa.
    """

    ROL_DUENO_CANCHA = "dueno_cancha"
    ROL_LIGA = "liga"
    ROL_CHOICES = [
        (ROL_DUENO_CANCHA, "Dueño de cancha"),
        (ROL_LIGA, "Representante de Liga"),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organizador",
    )
    rol = models.CharField(
        max_length=20,
        choices=ROL_CHOICES,
        default=ROL_LIGA,
        help_text="Rol elegido al registrarse.",
    )
    tiene_cancha = models.BooleanField(
        default=False,
        help_text="Gestiona cancha(s) propia(s). Habilita el Turnero de canchas.",
    )
    es_liga = models.BooleanField(
        default=False,
        help_text="También representa una Liga.",
    )
    nombre_cancha = models.CharField(
        max_length=100, blank=True, help_text="Nombre de la cancha (si tiene_cancha)."
    )
    nombre_liga = models.CharField(
        max_length=100, blank=True, help_text="Nombre de la Liga (si es_liga)."
    )
    celular = models.CharField(max_length=30, blank=True)
    logo = models.ImageField(upload_to="organizadores/logos/", blank=True, null=True)
    color_primario = models.CharField(
        max_length=7, blank=True, help_text="Color de marca en hex, ej: #1B5E20"
    )
    color_secundario = models.CharField(max_length=7, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Organizador"
        verbose_name_plural = "Organizadores"

    def save(self, *args, **kwargs):
        if self.rol == self.ROL_DUENO_CANCHA:
            self.tiene_cancha = True
        elif self.rol == self.ROL_LIGA:
            self.es_liga = True
        super().save(*args, **kwargs)

    @property
    def nombre_publico(self):
        """Nombre a mostrar: prioriza el de Liga, luego el de cancha."""
        return self.nombre_liga or self.nombre_cancha or self.usuario.get_username()

    def __str__(self):
        return self.nombre_publico


class Jugador(models.Model):
    """
    Perfil de Jugador: usa el marcador simple con diseño fijo GPADEL
    (sin personalizar) y, a futuro, reserva canchas y se inscribe a torneos.
    """

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="jugador",
    )
    nombre = models.CharField(max_length=100, blank=True)
    celular = models.CharField(max_length=30, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Jugador"
        verbose_name_plural = "Jugadores"

    def __str__(self):
        return self.nombre or self.usuario.get_username()
