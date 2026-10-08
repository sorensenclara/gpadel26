import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Identidad(models.Model):
    """
    PERSONA deportiva, tenga o no cuenta en GPADEL. El DNI es su identificador.

    - Con cuenta (`usuario`): es la identidad de esa cuenta, independiente de qué
      perfil(es) tenga (Jugador, Organizador, o ambos). `ultimo_perfil` permite
      volver al mismo contexto en el próximo ingreso.
    - Sin cuenta: una persona que participa porque su compañero la inscribió con
      DNI y nombre. Conserva su historial deportivo aunque nunca se registre.

    Una cuenta NUNCA se apropia de una identidad existente por escribir un DNI
    coincidente: debe pasar por un `ReclamoIdentidad` verificado por el staff.
    Las identidades no se fusionan ni se borran automáticamente.
    """

    PERFIL_JUGADOR = "jugador"
    PERFIL_ORGANIZADOR = "organizador"
    PERFIL_CHOICES = [
        (PERFIL_JUGADOR, "Jugador"),
        (PERFIL_ORGANIZADOR, "Organizador"),
    ]

    ORIGEN_CUENTA = "cuenta"
    ORIGEN_INSCRIPCION = "inscripcion"
    ORIGEN_MIGRACION = "migracion"
    ORIGEN_IMPORTACION = "importacion_ranking"
    ORIGEN_CHOICES = [
        (ORIGEN_CUENTA, "Alta de cuenta"),
        (ORIGEN_INSCRIPCION, "Inscripción de un compañero"),
        (ORIGEN_MIGRACION, "Migración de datos históricos"),
        (ORIGEN_IMPORTACION, "Importación de ranking"),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,  # borrar una cuenta jamás borra el historial deportivo
        null=True,
        blank=True,
        related_name="identidad",
    )
    dni = models.CharField(max_length=20, unique=True, help_text="Como se tipeó la primera vez (dato histórico).")
    dni_normalizado = models.CharField(
        max_length=20, blank=True, default="", db_index=True, editable=False,
        help_text="Solo dígitos. Es el que se usa para comparar.",
    )
    dni_en_revision = models.BooleanField(
        default=False,
        help_text="DNI ambiguo (varias identidades lo comparten): no participa de la unicidad hasta resolverlo.",
    )
    nombre = models.CharField(
        max_length=150, blank=True,
        help_text="Apellido y nombre declarados. Se usa solo para identidades sin cuenta; "
        "con cuenta manda el nombre del usuario.",
    )
    localidad = models.CharField(
        max_length=100, blank=True,
        help_text="Ciudad/localidad de la persona. Se pide en el registro; queda en blanco en "
        "cuentas creadas antes de agregar este campo.",
    )
    origen = models.CharField(max_length=20, choices=ORIGEN_CHOICES, default=ORIGEN_CUENTA)
    ultimo_perfil = models.CharField(
        max_length=20, choices=PERFIL_CHOICES, blank=True,
        help_text="Último perfil usado (solo relevante si tiene los dos habilitados).",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Identidad"
        verbose_name_plural = "Identidades"
        constraints = [
            # Un DNI (comparado sin puntos) = una identidad. Los casos ambiguos ya
            # existentes quedan marcados (dni_en_revision) y se resuelven a mano.
            models.UniqueConstraint(
                fields=["dni_normalizado"],
                condition=~models.Q(dni_normalizado="") & models.Q(dni_en_revision=False),
                name="identidad_dni_normalizado_unico",
            ),
            # Una persona sin cuenta tiene que poder identificarse por nombre.
            models.CheckConstraint(
                condition=models.Q(usuario__isnull=False) | ~models.Q(nombre=""),
                name="identidad_sin_cuenta_requiere_nombre",
            ),
        ]

    def save(self, *args, **kwargs):
        self.dni_normalizado = normalizar_dni(self.dni)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "dni" in update_fields:
            kwargs["update_fields"] = list(set(update_fields) | {"dni_normalizado"})
        super().save(*args, **kwargs)

    @property
    def tiene_cuenta(self):
        return self.usuario_id is not None

    @property
    def nombre_completo(self):
        if self.usuario_id:
            return self.usuario.get_full_name().strip() or self.usuario.username
        return self.nombre

    def __str__(self):
        return f"{self.nombre_completo or '(sin nombre)'} (DNI {self.dni})"


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


# Escala oficial de categorías de pádel: 1 es la mejor (1ra), 8 la más baja
# (8va). Un jugador puede subir (número más chico) pero nunca bajarse
# manualmente (número más grande) de su categoría oficial.
NIVEL_CHOICES = [
    (1, "1ra"), (2, "2da"), (3, "3ra"), (4, "4ta"),
    (5, "5ta"), (6, "6ta"), (7, "7ma"), (8, "8va"),
]
NIVEL_LABELS = dict(NIVEL_CHOICES)


def normalizar_dni(valor):
    """'30.100.001' / '30 100 001' / '30100001' -> '30100001'."""
    return re.sub(r"\D", "", valor or "")


def _palabras_significativas(nombre):
    """Palabras de ≥3 letras, en minúsculas y sin acentos: 'Pérez, Juan Ma.' -> {'perez','juan'}."""
    sin_acentos = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode("ascii")
    return {p for p in re.findall(r"[a-z]+", sin_acentos.lower()) if len(p) >= 3}


def nombres_compatibles(a, b):
    """
    True/False según compartan al menos una palabra significativa (sin importar
    orden, acentos ni mayúsculas); None si alguno no tiene datos para comparar.

    IMPORTANTE: sirve únicamente para DETECTAR posibles correspondencias o
    posibles errores de tipeo. Una coincidencia de nombre (y de DNI) NO es
    verificación de identidad y nunca debe usarse para vincular por sí sola.
    """
    pa, pb = _palabras_significativas(a), _palabras_significativas(b)
    if not pa or not pb:
        return None
    return bool(pa & pb)


def dni_ya_registrado(dni, excluir_pk=None):
    """
    True si ya existe una CUENTA con ese DNI (comparado sin puntos). Una identidad
    sin cuenta (persona que solo participó) no cuenta: registrarse con su DNI no
    se rechaza, pero tampoco la reclama: abre un ReclamoIdentidad a verificar.
    """
    buscado = normalizar_dni(dni)
    if not buscado:
        return False
    candidatas = Identidad.objects.filter(dni_normalizado=buscado, usuario__isnull=False)
    if excluir_pk is not None:
        candidatas = candidatas.exclude(pk=excluir_pk)
    return candidatas.exists()


def dni_en_uso_por_identidad(dni, excluir_pk=None):
    """True si CUALQUIER identidad (con o sin cuenta) ya tiene ese DNI normalizado. Se usa para no
    pisar la identidad de otra persona al corregir un DNI (a diferencia de dni_ya_registrado,
    que solo mira cuentas y sirve para decidir si un registro nuevo se rechaza)."""
    buscado = normalizar_dni(dni)
    if not buscado:
        return False
    candidatas = Identidad.objects.filter(dni_normalizado=buscado)
    if excluir_pk is not None:
        candidatas = candidatas.exclude(pk=excluir_pk)
    return candidatas.exists()


def puede_subir_categoria(actual, nueva):
    """
    Regla única de la categoría oficial: solo se puede SUBIR (número menor),
    nunca bajar. Sin categoría previa (None) cualquiera es válida.
    1ra = 1 es la más alta; 8va = 8 la más baja.
    """
    return actual is None or nueva is None or nueva <= actual


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
    categoria_oficial = models.PositiveSmallIntegerField(
        choices=NIVEL_CHOICES, null=True, blank=True,
        help_text="1ra es la categoría más alta. Se carga en la primera inscripción a un "
        "torneo y solo se puede mejorar (bajar el número) después, nunca empeorar.",
    )
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Jugador"
        verbose_name_plural = "Jugadores"

    def __str__(self):
        return self.nombre or self.usuario.get_username()


class ReclamoIdentidad(models.Model):
    """
    Una cuenta que dice ser una identidad deportiva que YA existe (la misma
    persona que alguien inscribió antes con su DNI). Mientras está pendiente
    NO hay vínculo efectivo: la cuenta no accede al historial ni gana permisos
    sobre esa identidad, y la identidad sigue sin cuenta.

    La coincidencia de DNI y de nombre solo genera `indicios` para orientar al
    staff: nunca constituye verificación. Verificar es una decisión explícita
    del staff (ver torneos.servicios_identidad.verificar_reclamo), que además
    elige la identidad: nada se infiere ni se fusiona automáticamente.
    """

    ESTADO_PENDIENTE = "pendiente"
    ESTADO_VERIFICADO = "verificado"
    ESTADO_RECHAZADO = "rechazado"
    ESTADO_EN_CONFLICTO = "en_conflicto"
    ESTADO_CHOICES = [
        (ESTADO_PENDIENTE, "Pendiente de verificación"),
        (ESTADO_VERIFICADO, "Verificado"),
        (ESTADO_RECHAZADO, "Rechazado"),
        (ESTADO_EN_CONFLICTO, "En conflicto (otra cuenta ya fue verificada)"),
    ]
    ESTADOS_ABIERTOS = (ESTADO_PENDIENTE, ESTADO_EN_CONFLICTO)

    METODO_DOCUMENTO_EN_SEDE = "documento_en_sede"
    METODO_DOCUMENTO_DIGITAL = "documento_digital"
    METODO_OTRO = "otro"
    METODO_CHOICES = [
        (METODO_DOCUMENTO_EN_SEDE, "Documento presentado en sede"),
        (METODO_DOCUMENTO_DIGITAL, "Documento enviado digitalmente"),
        (METODO_OTRO, "Otro (detallar en la nota)"),
    ]

    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reclamos_identidad")
    persona = models.ForeignKey(
        "Identidad", null=True, blank=True, on_delete=models.PROTECT, related_name="reclamos",
        help_text="Candidata única, si la hay. Vacía cuando hay varias o el DNI está en revisión.",
    )
    candidatas = models.ManyToManyField("Identidad", blank=True, related_name="reclamos_como_candidata")
    dni_declarado = models.CharField(max_length=20)
    dni_normalizado = models.CharField(max_length=20, blank=True, default="", db_index=True, editable=False)
    nombre_declarado = models.CharField(max_length=150, blank=True)
    localidad_declarada = models.CharField(max_length=100, blank=True)
    indicios = models.JSONField(default=dict, blank=True, help_text="Orientación para el staff; NO es verificación.")
    estado = models.CharField(max_length=14, choices=ESTADO_CHOICES, default=ESTADO_PENDIENTE)
    metodo_verificacion = models.CharField(max_length=20, choices=METODO_CHOICES, blank=True)
    nota_resolucion = models.TextField(blank=True)
    resuelto_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    resuelto_en = models.DateTimeField(null=True, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en"]
        verbose_name = "reclamo de identidad"
        verbose_name_plural = "reclamos de identidad"
        constraints = [
            # Una cuenta tiene a lo sumo un reclamo abierto.
            models.UniqueConstraint(
                fields=["usuario"],
                condition=models.Q(estado__in=["pendiente", "en_conflicto"]),
                name="reclamo_un_abierto_por_cuenta",
            ),
            # Una identidad se verifica para una sola cuenta.
            models.UniqueConstraint(
                fields=["persona"],
                condition=models.Q(estado="verificado"),
                name="reclamo_un_verificado_por_persona",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(estado__in=["pendiente", "en_conflicto"], resuelto_en__isnull=True)
                    | models.Q(estado="rechazado", resuelto_en__isnull=False)
                    | models.Q(estado="verificado", resuelto_en__isnull=False, persona__isnull=False)
                ),
                name="reclamo_resolucion_coherente",
            ),
        ]

    def save(self, *args, **kwargs):
        self.dni_normalizado = normalizar_dni(self.dni_declarado)
        super().save(*args, **kwargs)

    @property
    def abierto(self):
        return self.estado in self.ESTADOS_ABIERTOS

    def __str__(self):
        return f"Reclamo de {self.usuario} sobre DNI {self.dni_declarado} ({self.get_estado_display()})"


class EventoIdentidad(models.Model):
    """Registro append-only de quién hizo qué y cuándo con identidades y reclamos."""

    ACCIONES = [
        ("cuenta_con_identidad", "Cuenta registrada con identidad propia"),
        ("persona_creada", "Identidad sin cuenta creada"),
        ("reclamo_creado", "Reclamo de identidad abierto"),
        ("reclamo_verificado", "Reclamo verificado por el staff"),
        ("reclamo_rechazado", "Reclamo rechazado por el staff"),
        ("reclamo_en_conflicto", "Reclamo marcado en conflicto"),
        ("identidad_propia_creada", "Identidad propia creada tras un rechazo"),
        ("revision_resuelta", "Caso de revisión de DNI resuelto"),
    ]

    accion = models.CharField(max_length=30, choices=ACCIONES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Quién lo hizo (vacío si fue el sistema).",
    )
    cuenta = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Cuenta involucrada.",
    )
    persona = models.ForeignKey("Identidad", null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos")
    reclamo = models.ForeignKey(ReclamoIdentidad, null=True, blank=True, on_delete=models.SET_NULL, related_name="eventos")
    detalle = models.JSONField(default=dict, blank=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        verbose_name = "evento de identidad"
        verbose_name_plural = "eventos de identidad"

    def __str__(self):
        return f"{self.get_accion_display()} ({self.creado_en:%d/%m/%Y %H:%M})"


class MembresiaOrganizador(models.Model):
    """
    Pertenencia de una cuenta a una entidad (Organizador). Por sí sola NO otorga
    ningún permiso: cada permiso es una fila de PermisoMembresia, por área. Así
    una entidad no depende de una única cuenta y ser parte de ella no habilita
    a tocar todos sus recursos.
    """

    organizador = models.ForeignKey(Organizador, on_delete=models.CASCADE, related_name="membresias")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membresias_organizador")
    activa = models.BooleanField(default=True)
    otorgada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    creada_en = models.DateTimeField(auto_now_add=True)

    def _es_ultima_administradora_de_miembros(self):
        """True si esta membresía es la ÚNICA activa que administra miembros de su entidad."""
        administradoras = MembresiaOrganizador.objects.filter(
            organizador_id=self.organizador_id, activa=True,
            permisos__area=PermisoMembresia.AREA_MIEMBROS, permisos__nivel=PermisoMembresia.NIVEL_ADMINISTRAR,
        )
        return administradoras.filter(pk=self.pk).exists() and administradoras.count() == 1

    def save(self, *args, **kwargs):
        if self.pk and not self.activa:
            if MembresiaOrganizador.objects.filter(pk=self.pk, activa=True).exists() and self._es_ultima_administradora_de_miembros():
                raise ValidationError("La entidad no puede quedarse sin quien administre sus miembros.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._es_ultima_administradora_de_miembros():
            raise ValidationError("La entidad no puede quedarse sin quien administre sus miembros.")
        return super().delete(*args, **kwargs)

    class Meta:
        verbose_name = "membresía de organizador"
        verbose_name_plural = "membresías de organizador"
        constraints = [
            models.UniqueConstraint(fields=["organizador", "usuario"], name="membresia_unica_por_cuenta_y_entidad"),
        ]

    def __str__(self):
        return f"{self.usuario} en {self.organizador}"


class PermisoMembresia(models.Model):
    """Un permiso explícito de una membresía sobre un área. Sin fila = sin permiso."""

    AREA_RANKINGS = "rankings"
    AREA_TORNEOS = "torneos"
    AREA_CANCHAS = "canchas"
    AREA_MIEMBROS = "miembros"
    AREA_CHOICES = [
        (AREA_RANKINGS, "Rankings"),
        (AREA_TORNEOS, "Torneos"),
        (AREA_CANCHAS, "Canchas"),
        (AREA_MIEMBROS, "Miembros y permisos"),
    ]
    NIVEL_USAR = "usar"
    NIVEL_ADMINISTRAR = "administrar"
    NIVEL_CHOICES = [(NIVEL_USAR, "Usar"), (NIVEL_ADMINISTRAR, "Administrar")]
    _ORDEN_NIVEL = {NIVEL_USAR: 1, NIVEL_ADMINISTRAR: 2}

    membresia = models.ForeignKey(MembresiaOrganizador, on_delete=models.CASCADE, related_name="permisos")
    area = models.CharField(max_length=10, choices=AREA_CHOICES)
    nivel = models.CharField(max_length=12, choices=NIVEL_CHOICES)

    def _es_el_ultimo_de_miembros(self):
        return (
            self.area == self.AREA_MIEMBROS
            and self.nivel == self.NIVEL_ADMINISTRAR
            and self.membresia._es_ultima_administradora_de_miembros()  # consulta la base (solo activas)
        )

    def save(self, *args, **kwargs):
        if self.pk:
            previo = PermisoMembresia.objects.filter(pk=self.pk).first()
            if previo is not None and previo._es_el_ultimo_de_miembros() and self.nivel != self.NIVEL_ADMINISTRAR:
                raise ValidationError("La entidad no puede quedarse sin quien administre sus miembros.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._es_el_ultimo_de_miembros():
            raise ValidationError("La entidad no puede quedarse sin quien administre sus miembros.")
        return super().delete(*args, **kwargs)

    class Meta:
        verbose_name = "permiso de membresía"
        verbose_name_plural = "permisos de membresía"
        constraints = [
            models.UniqueConstraint(fields=["membresia", "area"], name="permiso_unico_por_area"),
        ]

    def __str__(self):
        return f"{self.membresia}: {self.get_area_display()} ({self.get_nivel_display()})"


def nivel_de_permiso(usuario, organizador, area):
    """Nivel ('usar' / 'administrar') que tiene esta cuenta sobre un área de la
    entidad, o None. Solo cuentan membresías activas con un permiso EXPLÍCITO."""
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        return None
    permiso = PermisoMembresia.objects.filter(
        membresia__organizador=organizador, membresia__usuario=usuario, membresia__activa=True, area=area
    ).first()
    return permiso.nivel if permiso else None


def tiene_permiso(usuario, organizador, area, minimo=PermisoMembresia.NIVEL_USAR):
    """True si la cuenta tiene al menos el nivel `minimo` sobre esa área de la entidad."""
    nivel = nivel_de_permiso(usuario, organizador, area)
    orden = PermisoMembresia._ORDEN_NIVEL
    return nivel is not None and orden[nivel] >= orden[minimo]
