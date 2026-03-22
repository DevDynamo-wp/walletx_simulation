# apps/core/views_dashboard.py — WalletX Dashboard
"""
Vue du tableau de bord WalletX.

Deux endpoints :
  GET  /walletx/dashboard/       → HTML du dashboard
  GET  /walletx/dashboard/data/  → JSON pour le polling toutes les 5s

Architecture des données retournées :
  stats            : métriques globales (soldes NonviPay, nb transactions, etc.)
  comptes_mtn      : liste des comptes utilisateurs MTN
  comptes_moov     : liste des comptes utilisateurs MOOV
  journal          : 20 dernières transactions (tous opérateurs)
"""
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from django.http import JsonResponse

from apps.core.models import (
    CompteNonviPay, CompteUtilisateur, TransactionWalletX, OPERATEUR_CHOICES
)

logger = logging.getLogger(__name__)


# ── Helpers de sérialisation ───────────────────────────────────────────────────

def _decimal_str(value):
    """Convertit un Decimal en float JSON-sérialisable."""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _build_stats() -> dict:
    """Calcule les métriques globales du dashboard."""
    compte_mtn  = CompteNonviPay.get_instance('MTN_BEN')
    compte_moov = CompteNonviPay.get_instance('MOOV_BEN')

    solde_mtn  = _decimal_str(compte_mtn.solde)
    solde_moov = _decimal_str(compte_moov.solde)

    nb_users_mtn  = CompteUtilisateur.objects.filter(operateur='MTN_BEN').count()
    nb_users_moov = CompteUtilisateur.objects.filter(operateur='MOOV_BEN').count()

    aujourd_hui = timezone.now().date()
    nb_aujourd_hui = TransactionWalletX.objects.filter(
        created_at__date=aujourd_hui
    ).count()
    nb_transactions = TransactionWalletX.objects.count()

    # Volumes du jour par opérateur
    volume_mtn_depot  = _get_volume('MTN_BEN',  'DEPOT')
    volume_mtn_retrait = _get_volume('MTN_BEN', 'RETRAIT')
    volume_moov_depot  = _get_volume('MOOV_BEN', 'DEPOT')
    volume_moov_retrait = _get_volume('MOOV_BEN', 'RETRAIT')

    return {
        'solde_mtn':          solde_mtn,
        'solde_moov':         solde_moov,
        'total_nonvipay':     round(solde_mtn + solde_moov, 2),
        'nb_users_mtn':       nb_users_mtn,
        'nb_users_moov':      nb_users_moov,
        'nb_aujourd_hui':     nb_aujourd_hui,
        'nb_transactions':    nb_transactions,
        'volume_mtn_depot':   volume_mtn_depot,
        'volume_mtn_retrait': volume_mtn_retrait,
        'volume_moov_depot':  volume_moov_depot,
        'volume_moov_retrait': volume_moov_retrait,
    }


def _get_volume(operateur: str, sens: str) -> float:
    """Volume total des transactions du jour pour un opérateur et un sens."""
    from django.db.models import Sum
    aujourd_hui = timezone.now().date()
    result = TransactionWalletX.objects.filter(
        operateur=operateur,
        sens=sens,
        statut='SUCCESS',
        created_at__date=aujourd_hui,
    ).aggregate(total=Sum('montant'))
    return _decimal_str(result['total'] or Decimal('0'))


def _build_comptes(operateur: str) -> list:
    """Retourne la liste des comptes utilisateurs d'un opérateur."""
    comptes = CompteUtilisateur.objects.filter(
        operateur=operateur
    ).order_by('numero_telephone')

    return [
        {
            'id':        str(c.id),
            'nom':       c.nom_titulaire,
            'numero':    c.numero_telephone,
            'solde':     _decimal_str(c.solde),
            'est_actif': c.est_actif,
            'nb_tx':     c.transactions.filter(operateur=operateur).count(),
        }
        for c in comptes
    ]


def _build_journal(limit: int = 25) -> list:
    """Retourne les dernières transactions pour le journal."""
    transactions = TransactionWalletX.objects.select_related(
        'compte_utilisateur'
    ).order_by('-created_at')[:limit]

    return [
        {
            'id':                  str(tx.id),
            'reference_walletx':   tx.reference_walletx,
            'reference_externe':   tx.reference_externe,
            'operateur':           tx.operateur,
            'nom':                 tx.compte_utilisateur.nom_titulaire,
            'numero':              tx.compte_utilisateur.numero_telephone,
            'sens':                tx.sens,
            'montant':             _decimal_str(tx.montant),
            'solde_np_apres':      _decimal_str(tx.solde_nonvipay_apres),
            'solde_user_apres':    _decimal_str(tx.solde_user_apres),
            'statut':              tx.statut,
            'webhook_envoye':      tx.webhook_envoye,
            'heure':               tx.created_at.strftime('%H:%M:%S'),
            'date':                tx.created_at.strftime('%d/%m %H:%M'),
        }
        for tx in transactions
    ]


# ── Vues ──────────────────────────────────────────────────────────────────────

class DashboardView(View):
    """
    GET /walletx/dashboard/
    Retourne le HTML du dashboard avec les données initiales injectées.
    Pas d'auth JWT — le dashboard est interne, accès local seulement.
    """
    def get(self, request):
        stats       = _build_stats()
        comptes_mtn  = _build_comptes('MTN_BEN')
        comptes_moov = _build_comptes('MOOV_BEN')
        journal      = _build_journal()

        context = {
            # Données brutes pour le template Django (rendu serveur)
            'stats':       stats,
            'comptes_mtn':  comptes_mtn,
            'comptes_moov': comptes_moov,
            'journal':      journal,

            # JSON injecté dans <script> pour le polling JS
            'stats_json':        json.dumps(stats),
            'comptes_mtn_json':  json.dumps(comptes_mtn),
            'comptes_moov_json': json.dumps(comptes_moov),
            'journal_json':      json.dumps(journal),

            # Infos serveur
            'debug': settings.DEBUG,
        }
        return render(request, 'core/dashboard.html', context)


class DashboardDataView(View):
    """
    GET /walletx/dashboard/data/
    Endpoint JSON pour le polling toutes les 5s depuis le dashboard.
    """
    def get(self, request):
        data = {
            'stats':        _build_stats(),
            'comptes_mtn':  _build_comptes('MTN_BEN'),
            'comptes_moov': _build_comptes('MOOV_BEN'),
            'journal':      _build_journal(),
        }
        return JsonResponse(data)