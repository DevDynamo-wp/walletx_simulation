from django.urls import path
from apps.core.views import (
    DepotView,
    RetraitView,
    SoldeView,
    RechargerView,
    HistoriqueView,
    StatutTransactionView,
    ListeComptesView,
)

urlpatterns = [
    path('depot/', DepotView.as_view(), name='walletx-depot'),
    path('retrait/', RetraitView.as_view(), name='walletx-retrait'),
    path('solde/', SoldeView.as_view(), name='walletx-solde'),
    path('recharger/', RechargerView.as_view(), name='walletx-recharger'),
    path('historique/', HistoriqueView.as_view(), name='walletx-historique'),
    path('transaction/<str:reference_externe>/', StatutTransactionView.as_view(), name='walletx-statut'),
    path('comptes/', ListeComptesView.as_view(), name='walletx-comptes'),
]