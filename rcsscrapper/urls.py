import os
from django.conf import settings
from django.contrib import admin
from django.urls import path, re_path
from django.contrib.staticfiles.views import serve as static_serve
from rcsscrapper import views

urlpatterns = [\
    path('admin/', admin.site.urls),
    
    # Splitwise Core Routes
    path('', views.splitwise_dashboard, name='splitwise_dashboard'),
    path('profile/', views.profile_view, name='profile'),
    path('expenses/add/', views.add_expense, name='add_expense'),
    path('expenses/settle/', views.settle_up, name='settle_up'),
    path('expenses/<int:expense_id>/delete/', views.delete_expense, name='delete_expense'),

    # Household Groups & Switching
    path('groups/', views.group_list, name='group_list'),
    path('groups/create/', views.create_group, name='create_group'),
    path('groups/<int:group_id>/select/', views.select_group, name='select_group'),
    path('groups/import-splitwise/', views.import_splitwise_view, name='import_splitwise'),

    # Authentication Routes
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('forgot-password/', views.forgot_password_view, name='forgot_password'),

    # Superstore Grocery Inbox & Assign Routes
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('batch/assign/', views.batch_process, name='batch_process'),
    path('batch/summary/', views.batch_summary, name='batch_summary'),
    path('batch/export-pdf/', views.export_latex_pdf, name='export_latex_pdf'),
    path('api/ingest/', views.api_ingest_order, name='api_ingest_order'),
    path('analytics/', views.analytics_dashboard, name='analytics_dashboard'),
    path('receipt/<int:receipt_id>/toggle-archive/', views.toggle_archive_receipt, name='toggle_archive_receipt'),
    path('receipt/<int:receipt_id>/delete/', views.delete_receipt, name='delete_receipt'),

    # PWA Routes
    path('manifest.json', views.manifest_view, name='manifest'),
    path('sw.js', views.service_worker_view, name='service_worker'),

    # Static assets serving for all apps (including Django admin styles and scripts)
    re_path(r'^static/(?P<path>.*)$', static_serve, {'insecure': True}),
]
