# apps/core/urls.py — WalletX
from django.urls import path
from apps.core.views import (
    DepotView,
    RetraitView,
    SoldeUtilisateurView,
    SoldeNonviPayView,
    RechargerView,
    HistoriqueView,
    StatutTransactionView,
    ListeComptesView,
)

urlpatterns = [
    # Opérations (appelées par NonviPay)
    path('depot/',   DepotView.as_view(),   name='walletx-depot'),
    path('retrait/', RetraitView.as_view(), name='walletx-retrait'),

    # Consultation des soldes
    path('solde/',          SoldeUtilisateurView.as_view(), name='walletx-solde-user'),
    path('solde/nonvipay/', SoldeNonviPayView.as_view(),    name='walletx-solde-nonvipay'),

    # Outils de test
    path('recharger/',  RechargerView.as_view(),  name='walletx-recharger'),
    path('historique/', HistoriqueView.as_view(),  name='walletx-historique'),
    path('comptes/',    ListeComptesView.as_view(), name='walletx-comptes'),

    # Statut d'une transaction
    path('transaction/<str:reference_externe>/', StatutTransactionView.as_view(), name='walletx-statut'),
]