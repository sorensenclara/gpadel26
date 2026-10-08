from django.contrib.auth import get_user_model
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Identidad, MembresiaOrganizador, Organizador, PermisoMembresia


@receiver(post_save, sender=Organizador)
def crear_membresia_fundadora(sender, instance, created, raw=False, **kwargs):
    """
    Cada Organizador nuevo nace con una membresía para su cuenta fundadora y permisos
    EXPLÍCITOS de administrar en todas las áreas (lo mismo que esa cuenta ya podía hacer).
    A partir de ahí, ningún otro integrante recibe nada sin una regla explícita.
    """
    if not created or raw:
        return
    membresia, _ = MembresiaOrganizador.objects.get_or_create(
        organizador=instance, usuario_id=instance.usuario_id, defaults={"activa": True}
    )
    for area, _etiqueta in PermisoMembresia.AREA_CHOICES:
        PermisoMembresia.objects.get_or_create(
            membresia=membresia, area=area, defaults={"nivel": PermisoMembresia.NIVEL_ADMINISTRAR}
        )


@receiver(pre_delete, sender=get_user_model())
def conservar_nombre_de_la_identidad(sender, instance, **kwargs):
    """
    Borrar una cuenta NO borra su identidad deportiva ni su historial (la identidad pasa a ser
    una persona sin cuenta). Una persona sin cuenta se identifica por su nombre, así que se lo
    guarda antes de que la cuenta desaparezca.
    """
    identidad = Identidad.objects.filter(usuario=instance).first()
    if identidad is not None and not identidad.nombre:
        identidad.nombre = instance.get_full_name().strip() or instance.username
        identidad.save(update_fields=["nombre"])
