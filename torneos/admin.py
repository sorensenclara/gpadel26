from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from accounts.models import ReclamoIdentidad

from .models import Categoria, DisponibilidadInscripcion, Inscripcion, RevisionIdentidad, Torneo
from .servicios import resolver_revision
from .servicios_identidad import ReclamoInvalido, rechazar_reclamo, verificar_reclamo


class CategoriaInline(admin.TabularInline):
    model = Categoria
    extra = 0
    fields = ("nombre", "cupo_minimo", "sumatoria_minima", "etapa", "grupos_publicacion", "llave_publicacion")


@admin.register(Torneo)
class TorneoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "organizador", "ciudad", "fecha_inicio", "estado")
    list_filter = ("estado", "formato")
    search_fields = ("nombre", "ciudad", "codigo")
    inlines = [CategoriaInline]


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "torneo", "etapa", "grupos_publicacion", "llave_publicacion")
    list_filter = ("etapa", "grupos_publicacion", "llave_publicacion", "torneo")
    search_fields = ("nombre", "torneo__nombre")


class DisponibilidadInline(admin.TabularInline):
    model = DisponibilidadInscripcion
    extra = 0


@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ("nombre_1", "apellido_1", "nombre_2", "apellido_2", "torneo", "categoria", "estado", "metodo_pago", "creado")
    list_filter = ("estado", "metodo_pago", "torneo")
    search_fields = ("nombre_1", "apellido_1", "nombre_2", "apellido_2", "dni_1", "dni_2", "telefono")
    raw_id_fields = ("usuario", "usuario_2")
    inlines = [DisponibilidadInline]


class RevisionIdentidadForm(forms.ModelForm):
    class Meta:
        model = RevisionIdentidad
        fields = ("estado", "titular_verificado", "nota_resolucion")

    def clean(self):
        cleaned = super().clean()
        abierta_antes = self.instance.pk and self.instance.estado == RevisionIdentidad.ESTADO_ABIERTA
        if abierta_antes and cleaned.get("estado") == RevisionIdentidad.ESTADO_RESUELTA:
            if not (cleaned.get("nota_resolucion") or "").strip():
                self.add_error("nota_resolucion", "Dejá constancia de cómo se verificó la identidad.")
            titular = cleaned.get("titular_verificado")
            if titular is not None and not self.instance.cuentas.filter(pk=titular.pk).exists():
                self.add_error("titular_verificado", "Tiene que ser una de las cuentas que comparten ese DNI.")
        return cleaned


@admin.register(RevisionIdentidad)
class RevisionIdentidadAdmin(admin.ModelAdmin):
    """
    Casos de identidad que no se resuelven solos. Se cierran DESPUÉS de
    verificar la identidad real de la persona: se elige la cuenta titular
    (o ninguna) y se deja constancia. Al resolver con titular se le vinculan
    las inscripciones históricas que la nombraban por DNI.

    Ojo: esto NO corrige el DNI de las otras cuentas; si siguen duplicadas,
    el próximo uso de ese DNI vuelve a abrir un caso (falla del lado seguro).
    """

    form = RevisionIdentidadForm
    list_display = ("dni", "motivo", "estado", "cantidad_cuentas", "detectada_en", "resuelta_en")
    list_filter = ("estado", "motivo")
    search_fields = ("dni",)
    readonly_fields = ("dni", "motivo", "detectada_en", "cuentas_involucradas", "inscripciones_afectadas",
                       "resuelta_por", "resuelta_en")
    fields = ("dni", "motivo", "detectada_en", "cuentas_involucradas", "inscripciones_afectadas",
              "estado", "titular_verificado", "nota_resolucion", "resuelta_por", "resuelta_en")

    @admin.display(description="Cuentas")
    def cantidad_cuentas(self, obj):
        return obj.cuentas.count()

    @admin.display(description="Cuentas que comparten el DNI")
    def cuentas_involucradas(self, obj):
        return ", ".join(f"{u.username} (id {u.pk})" for u in obj.cuentas.all()) or "—"

    @admin.display(description="Inscripciones afectadas")
    def inscripciones_afectadas(self, obj):
        return ", ".join(str(i) for i in obj.inscripciones.all()) or "—"

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.estado == RevisionIdentidad.ESTADO_RESUELTA:
            base += ["estado", "titular_verificado", "nota_resolucion"]
        return base

    def has_add_permission(self, request):
        return False  # los casos los abre el sistema, no se cargan a mano

    def save_model(self, request, obj, form, change):
        previo = RevisionIdentidad.objects.filter(pk=obj.pk).first() if change else None
        if (
            previo is not None
            and previo.estado == RevisionIdentidad.ESTADO_ABIERTA
            and obj.estado == RevisionIdentidad.ESTADO_RESUELTA
        ):
            vinculadas = resolver_revision(previo, obj.titular_verificado, request.user, obj.nota_resolucion)
            self.message_user(
                request, f"Caso resuelto. Inscripciones vinculadas a la cuenta titular: {vinculadas}.",
                level=messages.SUCCESS,
            )
            return
        super().save_model(request, obj, form, change)


class ReclamoIdentidadForm(forms.ModelForm):
    """Resolución por el staff. Elegir la identidad es una decisión EXPLÍCITA (nada se infiere)."""

    ACCIONES = [("", "— sin cambios —"), ("verificar", "Verificar identidad"), ("rechazar", "Rechazar reclamo")]
    accion = forms.ChoiceField(choices=ACCIONES, required=False)
    persona_elegida = forms.ModelChoiceField(
        queryset=None, required=False, help_text="Una de las candidatas. Solo si se verifica."
    )

    class Meta:
        model = ReclamoIdentidad
        fields = ("metodo_verificacion", "nota_resolucion")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "persona_elegida" in self.fields:  # el admin lo quita cuando el reclamo ya está resuelto
            self.fields["persona_elegida"].queryset = (
                self.instance.candidatas.all() if self.instance.pk else self.fields["persona_elegida"].queryset.none()
            )

    def clean(self):
        cleaned = super().clean()
        accion = cleaned.get("accion")
        if not accion:
            return cleaned
        if not self.instance.abierto:
            raise ValidationError("Ese reclamo ya fue resuelto.")
        if not (cleaned.get("nota_resolucion") or "").strip():
            self.add_error("nota_resolucion", "Dejá constancia de cómo se resolvió.")
        if accion == "verificar":
            if not cleaned.get("persona_elegida"):
                self.add_error("persona_elegida", "Elegí la identidad que se verificó.")
            elif cleaned["persona_elegida"].usuario_id:
                self.add_error("persona_elegida", "Esa identidad ya tiene una cuenta vinculada.")
            if not cleaned.get("metodo_verificacion"):
                self.add_error("metodo_verificacion", "Indicá cómo se verificó la identidad.")
            if self.instance.estado != ReclamoIdentidad.ESTADO_PENDIENTE:
                raise ValidationError("Un reclamo en conflicto solo se puede rechazar.")
        return cleaned


@admin.register(ReclamoIdentidad)
class ReclamoIdentidadAdmin(admin.ModelAdmin):
    """
    Cola del staff. Los indicios (DNI/nombre) orientan pero NO verifican. Al verificar se
    vincula la cuenta a la identidad elegida y se completa su historial; al rechazar no cambia
    ninguna identidad. Todo queda en la auditoría de identidades.
    """

    form = ReclamoIdentidadForm
    list_display = ("usuario", "dni_declarado", "estado", "creado_en", "resuelto_por")
    list_filter = ("estado",)
    search_fields = ("dni_declarado", "dni_normalizado", "usuario__username")
    readonly_fields = ("usuario", "dni_declarado", "nombre_declarado", "localidad_declarada", "indicios",
                       "candidatas_listadas", "estado", "creado_en", "resuelto_por", "resuelto_en")
    fields = ("usuario", "dni_declarado", "nombre_declarado", "localidad_declarada", "indicios", "candidatas_listadas",
              "estado", "creado_en", "accion", "persona_elegida", "metodo_verificacion", "nota_resolucion",
              "resuelto_por", "resuelto_en")

    @admin.display(description="Identidades candidatas")
    def candidatas_listadas(self, obj):
        return "; ".join(f"#{c.pk} {c.nombre_completo} (DNI {c.dni}, {'con' if c.usuario_id else 'sin'} cuenta)"
                         for c in obj.candidatas.all()) or "—"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        base = list(super().get_readonly_fields(request, obj))
        if obj is not None and not obj.abierto:
            base += ["accion", "persona_elegida", "metodo_verificacion", "nota_resolucion"]
        return base

    def save_model(self, request, obj, form, change):
        accion = form.cleaned_data.get("accion")
        try:
            if accion == "verificar":
                verificar_reclamo(obj, form.cleaned_data["persona_elegida"], request.user,
                                  form.cleaned_data["metodo_verificacion"], form.cleaned_data["nota_resolucion"])
                self.message_user(request, "Reclamo verificado: la cuenta quedó vinculada a la identidad.", messages.SUCCESS)
            elif accion == "rechazar":
                rechazar_reclamo(obj, request.user, form.cleaned_data["nota_resolucion"])
                self.message_user(request, "Reclamo rechazado.", messages.SUCCESS)
        except ReclamoInvalido as error:
            self.message_user(request, str(error), messages.ERROR)
