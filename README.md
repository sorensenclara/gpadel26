
# Marcador de Pádel (Django + Channels)

Marcador web en tiempo real con **Django 3.2 + Channels 3** (WebSockets). Incluye:
- Página de **visualización** que se actualiza automáticamente sin recargar.
- Página de **control** para modificar nombres y juegos por set.
- Código de partido para compartir: cualquiera con el link de **ver** puede seguir el marcador.

## Requisitos (dev)
- Python 3.8+
- (Opcional) Redis para producción. En dev usamos capa in-memory.

## Instalación rápida
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # opcional, para usar /admin
python manage.py runserver 0.0.0.0:8000
```

Abrí `http://localhost:8000` y creá un partido. Usá el enlace **Ver marcador** para compartir.

## Producción
- Usá `daphne`/`uvicorn` para ASGI, y **Redis** como channel layer.
- Configurá `CHANNEL_LAYERS` con Redis (documentación de Channels).
- Serví estáticos con Nginx u otro proxy.
