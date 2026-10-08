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
    path("mis-inscripciones/", torneos_views.mis_inscripciones, name="mis_inscripciones"),
    # Antes que <slug:codigo>, que también matchearía "companero".
    path("torneos/companero/", torneos_views.categoria_companero, name="categoria_companero"),
    path("torneos/companeros/buscar/", torneos_views.buscar_companeros, name="buscar_companeros"),
    path("torneos/<slug:codigo>/", torneos_views.torneo_detalle, name="torneo_detalle"),
    path("ayuda/", views.AyudaJugadoresView.as_view(), name="ayuda"),
    path("contacto/", views.ContactoView.as_view(), name="contacto"),
    path("login/", accounts_views.login_view, name="login"),
    path("logout/", accounts_views.logout_view, name="logout"),
    path("registro/", accounts_views.registro_view, name="registro"),
    path("partidos-en-vivo/", views.PartidosVivoView.as_view(), name="partidos_vivo"),
]
