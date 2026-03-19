"""
apps/core/views.py — Endpoints WalletX

Imitent exactement l'API d'un opérateur Mobile Money réel.
Sécurité : clé API dans le header X-API-Key sur chaque requête.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from apps.core.models import CompteVirtuel, TransactionVirtuelle
from apps.core.services import (
    initier_depot,
    initier_retrait,
    consulter_solde,
    crediter_compte,
    get_ou_creer_compte,
)

logger = logging.getLogger(__name__)


# ── Vérification clé API ───────────────────────────────────────

class ApiKeyMixin:
    """Vérifie X-API-Key sur chaque requête avant de traiter."""
    permission_classes = [AllowAny]

    def dispatch(self, request, *args, **kwargs):
        cle_recue = request.headers.get('X-API-Key', '')
        cle_attendue = getattr(settings, 'WALLETX_API_KEY', 'walletx-dev-key-2026')
        if cle_recue != cle_attendue:
            return Response(
                {
                    'success': False,
                    'message': 'Clé API invalide ou manquante.',
                    'code': 'INVALID_API_KEY',
                },
                status=status.HTTP_401_UNAUTHORIZED
            )
        return super().dispatch(request, *args, **kwargs)


def _valider_montant(valeur) -> tuple:
    """
    Valide et convertit un montant.
    Retourne (montant, None) ou (None, message_erreur).
    """
    try:
        montant = Decimal(str(valeur))
        if montant <= 0:
            raise InvalidOperation
        return montant, None
    except (InvalidOperation, ValueError, TypeError):
        return None, 'Montant invalide. Doit être un nombre positif.'


# ══════════════════════════════════════════════════════════════
# POST /walletx/api/depot/
# NonviPay demande un dépôt : débite le compte virtuel
# ══════════════════════════════════════════════════════════════

class DepotView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/depot/

    Body :
    {
        "numero_telephone": "+22961000000",
        "montant": "5000",
        "reference_externe": "DEP-20260319-ABCD1234",
        "webhook_url": "http://localhost:8000/api/v1/mobilemoney/webhook/",
        "description": "Dépôt NonviPay"   ← optionnel
    }
    """

    def post(self, request):
        data = request.data

        # Validation champs requis
        requis = ['numero_telephone', 'montant', 'reference_externe', 'webhook_url']
        manquants = [f for f in requis if not data.get(f)]
        if manquants:
            return Response(
                {
                    'success': False,
                    'message': f'Champs manquants : {", ".join(manquants)}',
                    'code': 'MISSING_FIELDS',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        montant, erreur = _valider_montant(data.get('montant'))
        if erreur:
            return Response(
                {'success': False, 'message': erreur, 'code': 'INVALID_AMOUNT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultat = initier_depot(
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        # Mapper les codes d'erreur en HTTP
        code = resultat.get('code', '')
        http_status = (
            status.HTTP_400_BAD_REQUEST
            if code in ('INSUFFICIENT_FUNDS', 'INVALID_AMOUNT', 'ACCOUNT_DISABLED')
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response({'success': False, **resultat}, status=http_status)


# ══════════════════════════════════════════════════════════════
# POST /walletx/api/retrait/
# NonviPay demande un retrait : crédite le compte virtuel
# ══════════════════════════════════════════════════════════════

class RetraitView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/retrait/

    Body :
    {
        "numero_telephone": "+22961000000",
        "montant": "3000",
        "reference_externe": "RET-20260319-XYZ9876",
        "webhook_url": "http://localhost:8000/api/v1/mobilemoney/webhook/",
        "description": "Retrait NonviPay"  ← optionnel
    }
    """

    def post(self, request):
        data = request.data

        requis = ['numero_telephone', 'montant', 'reference_externe', 'webhook_url']
        manquants = [f for f in requis if not data.get(f)]
        if manquants:
            return Response(
                {
                    'success': False,
                    'message': f'Champs manquants : {", ".join(manquants)}',
                    'code': 'MISSING_FIELDS',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        montant, erreur = _valider_montant(data.get('montant'))
        if erreur:
            return Response(
                {'success': False, 'message': erreur, 'code': 'INVALID_AMOUNT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultat = initier_retrait(
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        return Response(
            {'success': False, **resultat},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )


# ══════════════════════════════════════════════════════════════
# GET /walletx/api/solde/?numero=...
# ══════════════════════════════════════════════════════════════

class SoldeView(ApiKeyMixin, APIView):
    """Consulte le solde d'un compte virtuel."""

    def get(self, request):
        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response(
                {'success': False, 'message': 'Paramètre `numero` requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        resultat = consulter_solde(numero)
        return Response({'success': True, **resultat})


# ══════════════════════════════════════════════════════════════
# POST /walletx/api/recharger/
# Recharge manuelle d'un compte virtuel (tests / admin)
# ══════════════════════════════════════════════════════════════

class RechargerView(ApiKeyMixin, APIView):
    """Crédite directement un compte virtuel pour les tests."""

    def post(self, request):
        numero = request.data.get('numero_telephone', '').strip()
        if not numero:
            return Response(
                {'success': False, 'message': 'numero_telephone requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        montant, erreur = _valider_montant(request.data.get('montant'))
        if erreur:
            return Response(
                {'success': False, 'message': erreur, 'code': 'INVALID_AMOUNT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultat = crediter_compte(numero, montant)
        return Response({'success': True, **resultat})


# ══════════════════════════════════════════════════════════════
# GET /walletx/api/historique/?numero=...
# ══════════════════════════════════════════════════════════════

class HistoriqueView(ApiKeyMixin, APIView):
    """Retourne les 50 dernières transactions d'un compte."""

    def get(self, request):
        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response(
                {'success': False, 'message': 'Paramètre `numero` requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            compte = CompteVirtuel.objects.get(numero_telephone=numero)
        except CompteVirtuel.DoesNotExist:
            return Response({
                'success': True,
                'solde_actuel': '0',
                'transactions': [],
                'total': 0,
            })

        transactions = TransactionVirtuelle.objects.filter(
            compte=compte
        ).order_by('-created_at')[:50]

        data = [
            {
                'id': str(tx.id),
                'reference_walletx': tx.reference_walletx,
                'reference_externe': tx.reference_externe,
                'sens': tx.sens,
                'montant': str(tx.montant),
                'solde_avant': str(tx.solde_avant),
                'solde_apres': str(tx.solde_apres),
                'statut': tx.statut,
                'description': tx.description,
                'webhook_envoye': tx.webhook_envoye,
                'date': tx.created_at.isoformat(),
            }
            for tx in transactions
        ]

        return Response({
            'success': True,
            'numero': numero,
            'solde_actuel': str(compte.solde),
            'transactions': data,
            'total': len(data),
        })


# ══════════════════════════════════════════════════════════════
# GET /walletx/api/transaction/<reference_externe>/
# ══════════════════════════════════════════════════════════════

class StatutTransactionView(ApiKeyMixin, APIView):
    """Retourne le statut d'une transaction par sa référence externe."""

    def get(self, request, reference_externe):
        try:
            tx = TransactionVirtuelle.objects.select_related('compte').get(
                reference_externe=reference_externe
            )
        except TransactionVirtuelle.DoesNotExist:
            return Response(
                {
                    'success': False,
                    'message': 'Transaction introuvable.',
                    'code': 'NOT_FOUND',
                },
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            'success': True,
            'reference_externe': tx.reference_externe,
            'reference_walletx': tx.reference_walletx,
            'numero': tx.compte.numero_telephone,
            'sens': tx.sens,
            'montant': str(tx.montant),
            'statut': tx.statut,
            'webhook_envoye': tx.webhook_envoye,
            'date': tx.created_at.isoformat(),
        })


# ══════════════════════════════════════════════════════════════
# GET /walletx/api/comptes/
# Liste tous les comptes virtuels (debug/admin)
# ══════════════════════════════════════════════════════════════

class ListeComptesView(ApiKeyMixin, APIView):
    """Liste tous les comptes virtuels existants."""

    def get(self, request):
        comptes = CompteVirtuel.objects.all().order_by('-created_at')
        data = [
            {
                'id': str(c.id),
                'numero': c.numero_telephone,
                'nom': c.nom_titulaire,
                'solde': str(c.solde),
                'est_actif': c.est_actif,
                'nb_transactions': c.transactions.count(),
                'created_at': c.created_at.isoformat(),
            }
            for c in comptes
        ]
        return Response({'success': True, 'comptes': data, 'total': len(data)})