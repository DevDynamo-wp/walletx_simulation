# apps/core/urls.py — WalletX (VERSION MULTI-OPÉRATEURS)
"""
Routing WalletX — Double système jumeau MTN + Moov.

Architecture :
  /walletx/api/mtn/   → endpoints MTN_BEN
  /walletx/api/moov/  → endpoints MOOV_BEN
  /walletx/api/       → endpoints communs (tous opérateurs)

Les vues génériques (DepotView, RetraitView, etc.) reçoivent
le paramètre `operateur_url` ('mtn' ou 'moov') et le convertissent
en code interne ('MTN_BEN' ou 'MOOV_BEN').
"""
from django.urls import path
from apps.core.views import (
    DepotView,
    RetraitView,
    SoldeUtilisateurView,
    SoldeNonviPayView,
    HistoriqueView,
    # Endpoints communs
    TousSoldesNonviPayView,
    RechargerView,
    ListeComptesView,
    StatutTransactionView,
    ResetSoldesTestView,
)

# ── Pattern commun pour les endpoints par opérateur ───────────────────────────
# '<str:operateur_url>' capture 'mtn' ou 'moov' dans l'URL
# La vue le convertit en 'MTN_BEN' ou 'MOOV_BEN'

urlpatterns = [

    # ── Endpoints MTN ─────────────────────────────────────────────────────────
    path('mtn/depot/',          DepotView.as_view(),           {'operateur_url': 'mtn'}, name='walletx-mtn-depot'),
    path('mtn/retrait/',        RetraitView.as_view(),         {'operateur_url': 'mtn'}, name='walletx-mtn-retrait'),
    path('mtn/solde/',          SoldeUtilisateurView.as_view(),{'operateur_url': 'mtn'}, name='walletx-mtn-solde-user'),
    path('mtn/solde/nonvipay/', SoldeNonviPayView.as_view(),   {'operateur_url': 'mtn'}, name='walletx-mtn-solde-np'),
    path('mtn/historique/',     HistoriqueView.as_view(),      {'operateur_url': 'mtn'}, name='walletx-mtn-historique'),

    # ── Endpoints Moov ────────────────────────────────────────────────────────
    path('moov/depot/',          DepotView.as_view(),           {'operateur_url': 'moov'}, name='walletx-moov-depot'),
    path('moov/retrait/',        RetraitView.as_view(),         {'operateur_url': 'moov'}, name='walletx-moov-retrait'),
    path('moov/solde/',          SoldeUtilisateurView.as_view(),{'operateur_url': 'moov'}, name='walletx-moov-solde-user'),
    path('moov/solde/nonvipay/', SoldeNonviPayView.as_view(),   {'operateur_url': 'moov'}, name='walletx-moov-solde-np'),
    path('moov/historique/',     HistoriqueView.as_view(),      {'operateur_url': 'moov'}, name='walletx-moov-historique'),

    # ── Endpoints communs (indépendants de l'opérateur) ───────────────────────
    path('soldes/',           TousSoldesNonviPayView.as_view(), name='walletx-tous-soldes'),
    path('recharger/',        RechargerView.as_view(),          name='walletx-recharger'),
    path('comptes/',          ListeComptesView.as_view(),       name='walletx-comptes'),
    path('reset-soldes/',     ResetSoldesTestView.as_view(),    name='walletx-reset-soldes'),
    path(
        'transaction/<str:reference_externe>/',
        StatutTransactionView.as_view(),
        name='walletx-statut'
    ),

    # ── Compatibilité rétrocompatible (anciens endpoints → MTN par défaut) ────
    # Ces URLs permettent aux anciens tests de continuer à fonctionner
    # pendant la migration. À supprimer une fois NonviPay mis à jour.
    path('depot/',   DepotView.as_view(),   {'operateur_url': 'mtn'}, name='walletx-depot-legacy'),
    path('retrait/', RetraitView.as_view(), {'operateur_url': 'mtn'}, name='walletx-retrait-legacy'),
]