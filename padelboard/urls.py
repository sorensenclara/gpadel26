from django.conf import settings
from django.conf.urls.static import static

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('allauth.urls')),
    path('cuenta/', include('accounts.urls')),
    path('marcador/', include('scoreboard.urls')),
    path('organizador/torneos/', include('torneos.urls')),
    path('', include('sitio.urls')),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
