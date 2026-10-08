from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from allauth.socialaccount.forms import SignupForm as SocialSignupFormBase

from .identidad import dar_identidad_a_cuenta
from .models import Identidad, Jugador, Organizador, dni_ya_registrado

User = get_user_model()

ROL_JUGADOR = "jugador"
ROL_CHOICES = [
    (ROL_JUGADOR, "Jugador"),
    (Organizador.ROL_DUENO_CANCHA, "Dueño de cancha"),
    (Organizador.ROL_LIGA, "Representante de Liga"),
]


def _validar_roles_cruzados(form, cleaned):
    """Validación compartida entre alta normal y alta con Google."""
    rol = cleaned.get("rol")
    if rol == Organizador.ROL_DUENO_CANCHA:
        if not cleaned.get("nombre_cancha"):
            form.add_error("nombre_cancha", "Indicá el nombre de tu cancha.")
        if cleaned.get("es_liga") and not cleaned.get("nombre_liga"):
            form.add_error("nombre_liga", "Indicá el nombre de tu Liga.")
    elif rol == Organizador.ROL_LIGA:
        if not cleaned.get("nombre_liga"):
            form.add_error("nombre_liga", "Indicá el nombre de tu Liga.")
        if cleaned.get("tiene_cancha") and not cleaned.get("nombre_cancha"):
            form.add_error("nombre_cancha", "Indicá el nombre de tu cancha.")
    return cleaned


def _crear_perfil_organizador(user, rol, cleaned):
    tiene_cancha = True if rol == Organizador.ROL_DUENO_CANCHA else cleaned.get("tiene_cancha", False)
    es_liga = True if rol == Organizador.ROL_LIGA else cleaned.get("es_liga", False)
    return Organizador.objects.create(
        usuario=user,
        rol=rol,
        celular=cleaned.get("celular", ""),
        tiene_cancha=tiene_cancha,
        es_liga=es_liga,
        nombre_cancha=cleaned.get("nombre_cancha", "") if tiene_cancha else "",
        nombre_liga=cleaned.get("nombre_liga", "") if es_liga else "",
    )


def _crear_perfil(user, cleaned):
    """
    Crea el perfil Jugador u Organizador según el rol elegido, y la
    Identidad (DNI + nombre/apellido) que identifica a la persona más allá
    de qué perfil tenga. Se usa en el alta inicial (una cuenta = una
    Identidad + un primer perfil).
    """
    user.first_name = cleaned.get("nombre", "")
    user.last_name = cleaned.get("apellido", "")
    user.save(update_fields=["first_name", "last_name"])

    rol = cleaned["rol"]
    perfil_identidad = Identidad.PERFIL_JUGADOR if rol == ROL_JUGADOR else Identidad.PERFIL_ORGANIZADOR
    # DNI libre -> identidad propia. DNI de una identidad existente sin cuenta -> la cuenta
    # NO se apropia de ella ni de su historial: queda un reclamo pendiente de verificación
    # del staff y la cuenta usa el sistema normalmente (ver accounts.identidad).
    identidad, reclamo = dar_identidad_a_cuenta(
        user, cleaned["dni"], localidad=cleaned.get("localidad", ""), ultimo_perfil=perfil_identidad
    )
    user.reclamo_identidad_creado = reclamo  # para que la vista avise (no se persiste)

    if rol == ROL_JUGADOR:
        return Jugador.objects.create(usuario=user, celular=cleaned.get("celular", ""))
    return _crear_perfil_organizador(user, rol, cleaned)


class _IdentidadFieldsMixin(forms.Form):
    """Nombre, apellido, DNI y localidad: identifican a la persona, no al perfil."""

    nombre = forms.CharField(
        max_length=100,
        label="Nombre",
        widget=forms.TextInput(attrs={"id": "gpRegNombrePersona", "autocomplete": "given-name"}),
    )
    apellido = forms.CharField(
        max_length=100,
        label="Apellido",
        widget=forms.TextInput(attrs={"id": "gpRegApellido", "autocomplete": "family-name"}),
    )
    dni = forms.CharField(
        max_length=20,
        label="DNI",
        widget=forms.TextInput(attrs={"id": "gpRegDni", "inputmode": "numeric"}),
    )
    localidad = forms.CharField(
        max_length=100,
        label="Localidad",
        widget=forms.TextInput(attrs={"id": "gpRegLocalidad", "autocomplete": "address-level2"}),
    )

    def clean_dni(self):
        dni = self.cleaned_data["dni"].strip()
        # Comparación normalizada: "30.100.001" y "30100001" son el mismo DNI.
        # Permitir ambos crearía cuentas ambiguas que después no se pueden vincular.
        if dni_ya_registrado(dni):
            raise forms.ValidationError("Ya existe una cuenta registrada con ese DNI.")
        return dni


class _RolCruzadoFieldsMixin(_IdentidadFieldsMixin):
    """Campos de identidad + rol + cruce Cancha/Liga + celular, compartidos
    por ambos formularios de alta (registro normal y registro con Google)."""

    rol = forms.ChoiceField(
        choices=ROL_CHOICES,
        widget=forms.RadioSelect(attrs={"id": "gpRegRol"}),
        initial=ROL_JUGADOR,
        label="Tipo de cuenta",
    )
    nombre_cancha = forms.CharField(
        max_length=100,
        required=False,
        label="Nombre de tu cancha",
        widget=forms.TextInput(attrs={"id": "gpRegNombreCancha"}),
    )
    nombre_liga = forms.CharField(
        max_length=100,
        required=False,
        label="Nombre de tu Liga",
        widget=forms.TextInput(attrs={"id": "gpRegNombreLiga"}),
    )
    celular = forms.CharField(
        max_length=30,
        label="Celular",
        widget=forms.TextInput(
            attrs={"id": "gpRegCelular", "autocomplete": "tel", "inputmode": "tel"}
        ),
    )
    tiene_cancha = forms.BooleanField(
        required=False,
        label="Además gestiono una cancha propia",
        widget=forms.CheckboxInput(attrs={"id": "gpRegTieneCancha"}),
    )
    es_liga = forms.BooleanField(
        required=False,
        label="Además soy Representante de Liga",
        widget=forms.CheckboxInput(attrs={"id": "gpRegEsLiga"}),
    )


class GPadelAuthenticationForm(AuthenticationForm):
    """Login de Organizador, con los mismos ids que ya usaba la maqueta (gpUser/gpPass)."""

    username = forms.CharField(
        label="Usuario",
        widget=forms.TextInput(
            attrs={"id": "gpUser", "autocomplete": "username", "autofocus": True}
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={"id": "gpPass", "autocomplete": "current-password"}
        ),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Usuario o contraseña incorrectos.",
        "inactive": "Esta cuenta está inactiva.",
    }


class RegistroForm(_RolCruzadoFieldsMixin):
    """
    Alta de cuenta con usuario/contraseña propios. El usuario elige qué
    tipo de cuenta quiere crear.
    - Jugador: usa el marcador simple GPADEL.
    - Dueño de cancha / Representante de Liga: perfil Organizador.
    """

    ROL_JUGADOR = ROL_JUGADOR  # compat con código existente

    username = forms.CharField(
        max_length=150,
        label="Usuario",
        widget=forms.TextInput(attrs={"id": "gpRegUser", "autocomplete": "username"}),
    )
    email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(attrs={"id": "gpRegEmail", "autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={"id": "gpRegPass1", "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Repetir contraseña",
        widget=forms.PasswordInput(
            attrs={"id": "gpRegPass2", "autocomplete": "new-password"}
        ),
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ya existe una cuenta con ese usuario.")
        return username

    def clean(self):
        cleaned = super().clean()
        _validar_roles_cruzados(self, cleaned)

        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        if p1 and len(p1) < 8:
            self.add_error("password1", "Usá al menos 8 caracteres.")
        return cleaned

    def save(self):
        data = self.cleaned_data
        user = User.objects.create_user(
            username=data["username"],
            email=data.get("email", ""),
            password=data["password1"],
        )
        perfil = _crear_perfil(user, data)
        return user, data["rol"], perfil


class SocialSignupForm(_RolCruzadoFieldsMixin, SocialSignupFormBase):
    """
    "Completar perfil" tras registrarse con Google: no pedimos usuario ni
    contraseña (los da Google) — pedimos identidad (nombre/apellido/DNI),
    celular y tipo de cuenta.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El username lo autogenera allauth a partir del email de Google;
        # no se lo mostramos al usuario.
        if "username" in self.fields:
            self.fields["username"].widget = forms.HiddenInput()
            self.fields["username"].required = False
        # El email viene fijo de Google y ya se muestra en el banner de la
        # página; no hace falta un input visible y editable para eso.
        if "email" in self.fields:
            self.fields["email"].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        _validar_roles_cruzados(self, cleaned)
        return cleaned

    def save(self, request):
        user = super().save(request)  # crea el User + vincula la cuenta de Google
        _crear_perfil(user, self.cleaned_data)
        return user


class ActivarJugadorForm(forms.Form):
    """Habilitar el perfil de Jugador en una cuenta que ya existe (ya tiene
    Identidad con nombre/apellido/DNI — acá solo falta el celular si no lo
    cargó antes)."""

    celular = forms.CharField(
        max_length=30,
        label="Celular",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )

    def save(self, user):
        return Jugador.objects.create(usuario=user, celular=self.cleaned_data["celular"])


class ActivarOrganizadorForm(forms.Form):
    """Habilitar el perfil de Organizador en una cuenta que ya existe."""

    ROL_CHOICES_ACTIVAR = [
        (Organizador.ROL_DUENO_CANCHA, "Dueño de cancha"),
        (Organizador.ROL_LIGA, "Representante de Liga"),
    ]

    rol = forms.ChoiceField(
        choices=ROL_CHOICES_ACTIVAR,
        widget=forms.RadioSelect,
        initial=Organizador.ROL_LIGA,
        label="Tipo de cuenta",
    )
    nombre_cancha = forms.CharField(max_length=100, required=False, label="Nombre de tu cancha")
    nombre_liga = forms.CharField(max_length=100, required=False, label="Nombre de tu Liga")
    celular = forms.CharField(
        max_length=30, label="Celular",
        widget=forms.TextInput(attrs={"autocomplete": "tel", "inputmode": "tel"}),
    )
    tiene_cancha = forms.BooleanField(required=False, label="Además gestiono una cancha propia")
    es_liga = forms.BooleanField(required=False, label="Además soy Representante de Liga")

    def clean(self):
        cleaned = super().clean()
        _validar_roles_cruzados(self, cleaned)
        return cleaned

    def save(self, user):
        return _crear_perfil_organizador(user, self.cleaned_data["rol"], self.cleaned_data)
