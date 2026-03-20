# apps/core/views.py — WalletX
"""
Endpoints WalletX — Simule une API d'opérateur Mobile Money.

Authentification : clé API dans le header X-API-Key.
"""
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from apps.core.models import CompteNonviPay, CompteUtilisateur, TransactionWalletX
from apps.core.services import (
    initier_depot,
    initier_retrait,
    consulter_solde_utilisateur,
    consulter_solde_nonvipay,
    crediter_compte_utilisateur,
    get_ou_creer_compte_utilisateur,
)

logger = logging.getLogger(__name__)


class ApiKeyMixin:
    """Vérifie X-API-Key sur chaque requête."""
    permission_classes = [AllowAny]

    def dispatch(self, request, *args, **kwargs):
        cle_recue   = request.headers.get('X-API-Key', '')
        cle_attendue = getattr(settings, 'WALLETX_API_KEY', 'walletx-dev-key-2026')
        if cle_recue != cle_attendue:
            return Response(
                {'success': False, 'message': 'Clé API invalide.', 'code': 'INVALID_API_KEY'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        return super().dispatch(request, *args, **kwargs)


def _valider_montant(valeur):
    try:
        montant = Decimal(str(valeur))
        if montant <= 0:
            raise InvalidOperation
        return montant, None
    except (InvalidOperation, ValueError, TypeError):
        return None, 'Montant invalide. Doit être un nombre positif.'


# ══════════════════════════════════════════════════════════════════════════════
# POST /walletx/api/depot/
# NonviPay demande un dépôt : User WalletX → NonviPay WalletX
# ══════════════════════════════════════════════════════════════════════════════

class DepotView(ApiKeyMixin, APIView):
    """
    Reçoit une demande de dépôt de NonviPay.
    Débite CompteUtilisateur et crédite CompteNonviPay.
    Envoie un webhook SUCCESS si tout va bien.
    """
    def post(self, request):
        data = request.data
        requis = ['numero_telephone', 'montant', 'reference_externe', 'webhook_url']
        manquants = [f for f in requis if not data.get(f)]
        if manquants:
            return Response(
                {'success': False, 'message': f'Champs manquants : {", ".join(manquants)}', 'code': 'MISSING_FIELDS'},
                status=status.HTTP_400_BAD_REQUEST
            )

        montant, erreur = _valider_montant(data.get('montant'))
        if erreur:
            return Response({'success': False, 'message': erreur, 'code': 'INVALID_AMOUNT'}, status=status.HTTP_400_BAD_REQUEST)

        resultat = initier_depot(
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        code = resultat.get('code', '')
        http_status = status.HTTP_400_BAD_REQUEST if code in ('INSUFFICIENT_FUNDS', 'INVALID_AMOUNT', 'ACCOUNT_DISABLED') else status.HTTP_422_UNPROCESSABLE_ENTITY
        return Response({'success': False, **resultat}, status=http_status)


# ══════════════════════════════════════════════════════════════════════════════
# POST /walletx/api/retrait/
# NonviPay demande un retrait : NonviPay WalletX → User WalletX
# ══════════════════════════════════════════════════════════════════════════════

class RetraitView(ApiKeyMixin, APIView):
    """
    Reçoit une demande de retrait de NonviPay.
    Débite CompteNonviPay et crédite CompteUtilisateur.
    Envoie un webhook SUCCESS si tout va bien.
    """
    def post(self, request):
        data = request.data
        requis = ['numero_telephone', 'montant', 'reference_externe', 'webhook_url']
        manquants = [f for f in requis if not data.get(f)]
        if manquants:
            return Response(
                {'success': False, 'message': f'Champs manquants : {", ".join(manquants)}', 'code': 'MISSING_FIELDS'},
                status=status.HTTP_400_BAD_REQUEST
            )

        montant, erreur = _valider_montant(data.get('montant'))
        if erreur:
            return Response({'success': False, 'message': erreur, 'code': 'INVALID_AMOUNT'}, status=status.HTTP_400_BAD_REQUEST)

        resultat = initier_retrait(
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        return Response({'success': False, **resultat}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


# ══════════════════════════════════════════════════════════════════════════════
# GET /walletx/api/solde/?numero=...
# ══════════════════════════════════════════════════════════════════════════════

class SoldeUtilisateurView(ApiKeyMixin, APIView):
    """Consulte le solde d'un compte utilisateur."""
    def get(self, request):
        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response({'success': False, 'message': 'Paramètre `numero` requis.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'success': True, **consulter_solde_utilisateur(numero)})


# ══════════════════════════════════════════════════════════════════════════════
# GET /walletx/api/solde/nonvipay/
# Solde du compte NonviPay chez WalletX (= GATEWAY_EXTERNAL de NonviPay)
# ══════════════════════════════════════════════════════════════════════════════

class SoldeNonviPayView(ApiKeyMixin, APIView):
    """
    Retourne le solde du compte NonviPay.
    Ce solde doit correspondre à GATEWAY_EXTERNAL.available_balanced dans NonviPay.
    """
    def get(self, request):
        return Response({'success': True, **consulter_solde_nonvipay()})


# ══════════════════════════════════════════════════════════════════════════════
# POST /walletx/api/recharger/
# Recharge manuelle d'un compte utilisateur (tests)
# ══════════════════════════════════════════════════════════════════════════════

class RechargerView(ApiKeyMixin, APIView):
    """Recharge un compte utilisateur pour les tests."""
    def post(self, request):
        numero = request.data.get('numero_telephone', '').strip()
        if not numero:
            return Response({'success': False, 'message': 'numero_telephone requis.'}, status=status.HTTP_400_BAD_REQUEST)

        montant, erreur = _valider_montant(request.data.get('montant'))
        if erreur:
            return Response({'success': False, 'message': erreur}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'success': True, **crediter_compte_utilisateur(numero, montant)})


# ══════════════════════════════════════════════════════════════════════════════
# GET /walletx/api/historique/?numero=...
# ══════════════════════════════════════════════════════════════════════════════

class HistoriqueView(ApiKeyMixin, APIView):
    """Retourne les 50 dernières transactions d'un compte utilisateur."""
    def get(self, request):
        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response({'success': False, 'message': '`numero` requis.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            compte = CompteUtilisateur.objects.get(numero_telephone=numero)
        except CompteUtilisateur.DoesNotExist:
            return Response({'success': True, 'transactions': [], 'total': 0, 'solde_actuel': '0'})

        transactions = TransactionWalletX.objects.filter(
            compte_utilisateur=compte
        ).order_by('-created_at')[:50]

        data = [
            {
                'id': str(tx.id),
                'reference_walletx': tx.reference_walletx,
                'reference_externe': tx.reference_externe,
                'sens': tx.sens,
                'montant': str(tx.montant),
                'solde_user_avant': str(tx.solde_user_avant),
                'solde_user_apres': str(tx.solde_user_apres),
                'solde_nonvipay_apres': str(tx.solde_nonvipay_apres),
                'statut': tx.statut,
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


# ══════════════════════════════════════════════════════════════════════════════
# GET /walletx/api/transaction/<reference_externe>/
# ══════════════════════════════════════════════════════════════════════════════

class StatutTransactionView(ApiKeyMixin, APIView):
    def get(self, request, reference_externe):
        try:
            tx = TransactionWalletX.objects.select_related('compte_utilisateur').get(
                reference_externe=reference_externe
            )
        except TransactionWalletX.DoesNotExist:
            return Response(
                {'success': False, 'message': 'Transaction introuvable.', 'code': 'NOT_FOUND'},
                status=status.HTTP_404_NOT_FOUND
            )
        return Response({
            'success': True,
            'reference_externe': tx.reference_externe,
            'reference_walletx': tx.reference_walletx,
            'numero': tx.compte_utilisateur.numero_telephone,
            'sens': tx.sens,
            'montant': str(tx.montant),
            'statut': tx.statut,
            'webhook_envoye': tx.webhook_envoye,
            'solde_nonvipay_apres': str(tx.solde_nonvipay_apres),
            'date': tx.created_at.isoformat(),
        })


# ══════════════════════════════════════════════════════════════════════════════
# GET /walletx/api/comptes/
# ══════════════════════════════════════════════════════════════════════════════

class ListeComptesView(ApiKeyMixin, APIView):
    """Liste tous les comptes (NonviPay + utilisateurs)."""
    def get(self, request):
        # Compte NonviPay
        nonvipay = CompteNonviPay.get_instance()

        # Comptes utilisateurs
        comptes_users = CompteUtilisateur.objects.all().order_by('-created_at')
        data_users = [
            {
                'id': str(c.id),
                'numero': c.numero_telephone,
                'nom': c.nom_titulaire,
                'solde': str(c.solde),
                'est_actif': c.est_actif,
                'nb_transactions': c.transactions.count(),
            }
            for c in comptes_users
        ]

        return Response({
            'success': True,
            'compte_nonvipay': {
                'id': str(nonvipay.id),
                'nom': nonvipay.nom,
                'solde': str(nonvipay.solde),
                'info': 'Ce solde = GATEWAY_EXTERNAL dans NonviPay',
            },
            'comptes_utilisateurs': data_users,
            'total_utilisateurs': len(data_users),
        })