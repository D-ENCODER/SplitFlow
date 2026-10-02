"""
URL configuration for rcsscrapper project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from rcsscrapper import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.receipt_list, name='receipt_list'),
    path('batch/assign/', views.batch_process, name='batch_process'),
    path('batch/summary/', views.batch_summary, name='batch_summary'),
    path('batch/export-pdf/', views.export_latex_pdf, name='export_latex_pdf'),
    path('api/ingest/', views.api_ingest_order, name='api_ingest_order'),
    path('receipt/<int:receipt_id>/toggle-archive/', views.toggle_archive_receipt, name='toggle_archive_receipt'),
    path('receipt/<int:receipt_id>/delete/', views.delete_receipt, name='delete_receipt'),
    path('receipt/<int:receipt_id>/toggle-archive/', views.toggle_archive_receipt, name='toggle_archive_receipt'),
]