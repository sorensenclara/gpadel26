from django.views.generic import TemplateView


class InicioView(TemplateView):
    template_name = "sitio/inicio.html"


class JugadoresView(TemplateView):
    template_name = "sitio/jugadores.html"


class CanchasLigasView(TemplateView):
    """
    Sección Canchas & Ligas: si el visitante es un Organizador logueado,
    muestra su dashboard real (torneos, marcador, turnero). Si no, muestra
    la landing pública con la propuesta de valor y el CTA a registrarse.
    """

    def get_template_names(self):
        organizador = getattr(self.request.user, "organizador", None)
        if organizador:
            return ["sitio/organizador_dashboard.html"]
        return ["sitio/canchas_ligas.html"]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organizador = getattr(self.request.user, "organizador", None)
        if organizador:
            context["organizador"] = organizador
            context["torneos"] = organizador.torneos.all()[:5]
            context["torneos_count"] = organizador.torneos.count()
            context["partidos_count"] = organizador.partidos.count()
        return context


class AyudaJugadoresView(TemplateView):
    template_name = "sitio/ayuda_jugadores.html"


class ContactoView(TemplateView):
    template_name = "sitio/contacto.html"


class PartidosVivoView(TemplateView):
    """Listado de partidos en vivo. Todavía en construcción (maquetado)."""

    template_name = "sitio/construccion.html"
    extra_context = {
        "titulo_pagina": (
            "Partidos de pádel en vivo | Marcador y partido en tiempo "
            "real GPADEL"
        ),
        "meta_description": (
            "Seguí partidos de pádel en vivo con el marcador en tiempo "
            "real de GPADEL. Resultados, sets y evolución del partido en "
            "una sola pantalla."
        ),
    }
