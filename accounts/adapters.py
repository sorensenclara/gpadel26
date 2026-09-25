from allauth.account.adapter import DefaultAccountAdapter
from django.urls import reverse

from .models import Identidad


class GPadelAccountAdapter(DefaultAccountAdapter):
    """
    Tras loguearse (incluido el alta con Google): mismo criterio que el
    login con usuario/contraseña — un solo perfil, entra directo; los dos
    perfiles, entra al último que usó (ver accounts.views._perfil_a_usar).
    """

    def get_login_redirect_url(self, request):
        from .views import _perfil_a_usar  # evita import circular

        perfil = _perfil_a_usar(request.user)
        if perfil == Identidad.PERFIL_JUGADOR:
            return reverse("sitio:jugadores")
        if perfil == Identidad.PERFIL_ORGANIZADOR:
            return reverse("sitio:canchas_ligas")
        return reverse("scoreboard:index")
