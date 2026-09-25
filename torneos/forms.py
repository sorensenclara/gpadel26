from django import forms
from django.forms import formset_factory, modelformset_factory

from .models import Categoria, Inscripcion, Torneo


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
        return cleaned


class CategoriaForm(forms.ModelForm):
    """Paso 2: cada categoría posible + el mínimo de parejas para habilitarla."""

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

    class Meta:
        model = Inscripcion
        fields = [
            "categoria",
            "categoria_real",
            "nombre_1",
            "dni_1",
            "localidad_1",
            "socio_1",
            "club_socio_1",
            "carnet_socio_1",
            "nombre_2",
            "dni_2",
            "localidad_2",
            "socio_2",
            "club_socio_2",
            "carnet_socio_2",
            "telefono",
            "disponibilidad",
            "metodo_pago",
        ]
        widgets = {
            "metodo_pago": forms.RadioSelect,
        }

    def __init__(self, *args, torneo=None, **kwargs):
        super().__init__(*args, **kwargs)
        if torneo is not None:
            self.fields["categoria"].queryset = torneo.categorias.all()

    def save(self, torneo, commit=True):
        inscripcion = super().save(commit=False)
        inscripcion.torneo = torneo
        # Efectivo no confirma solo: lo tiene que habilitar el organizador
        # cuando reciba el pago. Mercado Pago/transferencia quedan
        # "pendiente" también por ahora (no hay pasarela automática todavía),
        # y el organizador confirma manualmente.
        inscripcion.estado = Inscripcion.ESTADO_PENDIENTE
        if commit:
            inscripcion.save()
        return inscripcion
