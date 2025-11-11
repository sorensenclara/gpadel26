
from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('nuevo/', views.new_match, name='new_match'),
    path('ver/<slug:code>/', views.viewer, name='viewer'),
    path('control/<slug:code>/', views.control, name='control'),
    path('api/<slug:code>/update/', views.update_scores, name='update_scores'),
]
