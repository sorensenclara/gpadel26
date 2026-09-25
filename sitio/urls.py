from django.urls import path

from accounts import views as accounts_views
from reservas import views as reservas_views
from torneos import views as torneos_views

from . import views

app_name = "sitio"

urlpatterns = [
    path("", views.InicioView.as_view(), name="inicio"),
    path("jugadores/", views.JugadoresView.as_view(), name="jugadores"),
    path("canchas-ligas/", views.CanchasLigasView.as_view(), name="canchas_ligas"),
    path("reservar-cancha/", reservas_views.buscar_canchas, name="reservar_cancha"),
    path("canchas/<int:cancha_id>/", reservas_views.cancha_detalle, name="cancha_detalle"),
    path("canchas/<int:cancha_id>/reservar/", reservas_views.reservar_turno, name="reservar_turno"),
    path("torneos/", torneos_views.listado_torneos, name="torneos"),
    path("torneos/<slug:codigo>/", torneos_views.torneo_detalle, name="torneo_detalle"),
    path("ayuda/", views.AyudaJugadoresView.as_view(), name="ayuda"),
    path("contacto/", views.ContactoView.as_view(), name="contacto"),
    path("login/", accounts_views.login_view, name="login"),
    path("logout/", accounts_views.logout_view, name="logout"),
    path("registro/", accounts_views.registro_view, name="registro"),
    path("partidos-en-vivo/", views.PartidosVivoView.as_view(), name="partidos_vivo"),
]
