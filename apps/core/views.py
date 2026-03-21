# apps/core/views.py — WalletX (VERSION MULTI-OPÉRATEURS)
"""
Endpoints WalletX — Double système jumeau MTN + Moov.

Authentification : clé API dans le header X-API-Key.

Structure des endpoints :

  ── MTN_BEN ──────────────────────────────────────────────────────
  POST /walletx/api/mtn/depot/          Dépôt MTN
  POST /walletx/api/mtn/retrait/        Retrait MTN
  GET  /walletx/api/mtn/solde/          Solde utilisateur MTN
  GET  /walletx/api/mtn/solde/nonvipay/ Solde NonviPay chez MTN
  GET  /walletx/api/mtn/historique/     Historique MTN

  ── MOOV_BEN ─────────────────────────────────────────────────────
  POST /walletx/api/moov/depot/         Dépôt Moov
  POST /walletx/api/moov/retrait/       Retrait Moov
  GET  /walletx/api/moov/solde/         Solde utilisateur Moov
  GET  /walletx/api/moov/solde/nonvipay/Solde NonviPay chez Moov
  GET  /walletx/api/moov/historique/    Historique Moov

  ── COMMUN ───────────────────────────────────────────────────────
  GET  /walletx/api/comptes/            Tous les comptes
  GET  /walletx/api/soldes/             Tous les soldes NonviPay
  POST /walletx/api/recharger/          Recharge manuelle (tests)
  POST /walletx/api/reset-soldes/       Reset complet (DEBUG uniquement)
  GET  /walletx/api/transaction/<ref>/  Statut d'une transaction
"""
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework import status

from apps.core.models import (
    CompteNonviPay, CompteUtilisateur, TransactionWalletX, OPERATEUR_CHOICES
)
from apps.core.services import (
    initier_depot,
    initier_retrait,
    consulter_solde_utilisateur,
    consulter_solde_nonvipay,
    consulter_tous_soldes_nonvipay,
    crediter_compte_utilisateur,
    get_ou_creer_compte_utilisateur,
    reset_soldes_test,
)

logger = logging.getLogger(__name__)

# Codes opérateurs supportés
OPERATEURS_SUPPORTES = {code for code, _ in OPERATEUR_CHOICES}


# ── Mixin de sécurité ──────────────────────────────────────────────────────────

class ApiKeyMixin:
    """Vérifie X-API-Key sur chaque requête WalletX."""
    permission_classes = [AllowAny]

    def dispatch(self, request, *args, **kwargs):
        cle_recue    = request.headers.get('X-API-Key', '')
        cle_attendue = getattr(settings, 'WALLETX_API_KEY', 'walletx-dev-key-2026')
        if cle_recue != cle_attendue:
            return Response(
                {
                    'success': False,
                    'message': 'Clé API invalide.',
                    'code': 'INVALID_API_KEY',
                },
                status=status.HTTP_401_UNAUTHORIZED
            )
        return super().dispatch(request, *args, **kwargs)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _valider_montant(valeur):
    """Valide et convertit un montant. Retourne (Decimal, None) ou (None, message)."""
    try:
        montant = Decimal(str(valeur))
        if montant <= 0:
            raise InvalidOperation
        return montant, None
    except (InvalidOperation, ValueError, TypeError):
        return None, 'Montant invalide. Doit être un nombre positif.'


def _verifier_operateur(operateur: str):
    """Vérifie que l'opérateur est supporté."""
    if operateur not in OPERATEURS_SUPPORTES:
        return Response(
            {
                'success': False,
                'message': f"Opérateur '{operateur}' non supporté. "
                           f"Opérateurs valides : {', '.join(sorted(OPERATEURS_SUPPORTES))}",
                'code': 'INVALID_OPERATOR',
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    return None


# ══════════════════════════════════════════════════════════════════════════════
# VUES GÉNÉRIQUES (réutilisées par MTN et Moov via le paramètre URL)
# ══════════════════════════════════════════════════════════════════════════════

class DepotView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/mtn/depot/
    POST /walletx/api/moov/depot/

    NonviPay demande un dépôt pour un opérateur donné.
    L'opérateur est passé via l'URL (ex: 'mtn' → 'MTN_BEN').
    """
    def post(self, request, operateur_url: str):
        operateur = _url_vers_operateur(operateur_url)
        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

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

        montant, erreur_montant = _valider_montant(data.get('montant'))
        if erreur_montant:
            return Response(
                {'success': False, 'message': erreur_montant, 'code': 'INVALID_AMOUNT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultat = initier_depot(
            operateur=operateur,
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        # Mapper les codes d'erreur sur les statuts HTTP appropriés
        code = resultat.get('code', '')
        http_status = (
            status.HTTP_400_BAD_REQUEST
            if code in ('INSUFFICIENT_FUNDS', 'INVALID_AMOUNT', 'ACCOUNT_DISABLED')
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response({'success': False, **resultat}, status=http_status)


class RetraitView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/mtn/retrait/
    POST /walletx/api/moov/retrait/
    """
    def post(self, request, operateur_url: str):
        operateur = _url_vers_operateur(operateur_url)
        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

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

        montant, erreur_montant = _valider_montant(data.get('montant'))
        if erreur_montant:
            return Response(
                {'success': False, 'message': erreur_montant, 'code': 'INVALID_AMOUNT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        resultat = initier_retrait(
            operateur=operateur,
            numero=data['numero_telephone'],
            montant=montant,
            reference_externe=data['reference_externe'],
            webhook_url=data['webhook_url'],
            description=data.get('description', ''),
        )

        if resultat.get('statut') == 'SUCCESS':
            return Response({'success': True, **resultat}, status=status.HTTP_200_OK)

        return Response({'success': False, **resultat}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


class SoldeUtilisateurView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/mtn/solde/?numero=+22997000001
    GET /walletx/api/moov/solde/?numero=+22961000001
    """
    def get(self, request, operateur_url: str):
        operateur = _url_vers_operateur(operateur_url)
        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response(
                {'success': False, 'message': 'Paramètre `numero` requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            'success': True,
            **consulter_solde_utilisateur(numero, operateur)
        })


class SoldeNonviPayView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/mtn/solde/nonvipay/
    GET /walletx/api/moov/solde/nonvipay/

    Retourne le solde du compte NonviPay chez un opérateur.
    Ce solde doit correspondre à GATEWAY_MTN ou GATEWAY_MOOV dans NonviPay.
    """
    def get(self, request, operateur_url: str):
        operateur = _url_vers_operateur(operateur_url)
        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

        return Response({
            'success': True,
            **consulter_solde_nonvipay(operateur)
        })


class HistoriqueView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/mtn/historique/?numero=+22997000001
    GET /walletx/api/moov/historique/?numero=+22961000001
    """
    def get(self, request, operateur_url: str):
        operateur = _url_vers_operateur(operateur_url)
        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

        numero = request.query_params.get('numero', '').strip()
        if not numero:
            return Response(
                {'success': False, 'message': '`numero` requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            compte = CompteUtilisateur.objects.get(
                numero_telephone=numero,
                operateur=operateur
            )
        except CompteUtilisateur.DoesNotExist:
            return Response({
                'success': True,
                'operateur': operateur,
                'transactions': [],
                'total': 0,
                'solde_actuel': '0',
                'info': f'Aucun compte {operateur} trouvé pour ce numéro.',
            })

        transactions = TransactionWalletX.objects.filter(
            compte_utilisateur=compte,
            operateur=operateur,
        ).order_by('-created_at')[:50]

        data = [
            {
                'id': str(tx.id),
                'reference_walletx': tx.reference_walletx,
                'reference_externe': tx.reference_externe,
                'operateur': tx.operateur,
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
            'operateur': operateur,
            'numero': numero,
            'solde_actuel': str(compte.solde),
            'transactions': data,
            'total': len(data),
        })


# ══════════════════════════════════════════════════════════════════════════════
# VUES COMMUNES (indépendantes de l'opérateur)
# ══════════════════════════════════════════════════════════════════════════════

class TousSoldesNonviPayView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/soldes/

    Vue d'ensemble : soldes NonviPay chez TOUS les opérateurs.
    Utile pour vérifier que GATEWAY_MTN + GATEWAY_MOOV = total réel.
    """
    def get(self, request):
        return Response({'success': True, **consulter_tous_soldes_nonvipay()})


class RechargerView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/recharger/

    Recharge un compte utilisateur (tests uniquement).
    Nécessite de préciser l'opérateur.

    Body :
      {
        "numero_telephone": "+22997000001",
        "montant": "50000",
        "operateur": "MTN_BEN"
      }
    """
    def post(self, request):
        numero   = request.data.get('numero_telephone', '').strip()
        operateur = request.data.get('operateur', '').strip()

        if not numero:
            return Response(
                {'success': False, 'message': 'numero_telephone requis.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        erreur = _verifier_operateur(operateur)
        if erreur:
            return erreur

        montant, erreur_montant = _valider_montant(request.data.get('montant'))
        if erreur_montant:
            return Response(
                {'success': False, 'message': erreur_montant},
                status=status.HTTP_400_BAD_REQUEST
            )

        return Response({
            'success': True,
            **crediter_compte_utilisateur(numero, montant, operateur)
        })


class ListeComptesView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/comptes/

    Liste tous les comptes (NonviPay + utilisateurs) par opérateur.
    """
    def get(self, request):
        # Comptes NonviPay par opérateur
        comptes_np = {}
        for compte in CompteNonviPay.objects.filter(nom='NonviPay').order_by('operateur'):
            comptes_np[compte.operateur] = {
                'id': str(compte.id),
                'solde': str(compte.solde),
                'est_actif': compte.est_actif,
            }

        # Comptes utilisateurs groupés par opérateur
        comptes_users = {}
        for code, libelle in OPERATEUR_CHOICES:
            comptes = CompteUtilisateur.objects.filter(
                operateur=code
            ).order_by('numero_telephone')
            comptes_users[code] = [
                {
                    'id': str(c.id),
                    'numero': c.numero_telephone,
                    'nom': c.nom_titulaire,
                    'solde': str(c.solde),
                    'est_actif': c.est_actif,
                    'nb_transactions': c.transactions.count(),
                }
                for c in comptes
            ]

        return Response({
            'success': True,
            'comptes_nonvipay': comptes_np,
            'comptes_utilisateurs': comptes_users,
            'operateurs_supportes': [code for code, _ in OPERATEUR_CHOICES],
        })


class StatutTransactionView(ApiKeyMixin, APIView):
    """
    GET /walletx/api/transaction/<reference_externe>/

    Statut d'une transaction par sa référence NonviPay.
    """
    def get(self, request, reference_externe):
        try:
            tx = TransactionWalletX.objects.select_related(
                'compte_utilisateur'
            ).get(reference_externe=reference_externe)
        except TransactionWalletX.DoesNotExist:
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
            'operateur': tx.operateur,
            'numero': tx.compte_utilisateur.numero_telephone,
            'sens': tx.sens,
            'montant': str(tx.montant),
            'statut': tx.statut,
            'webhook_envoye': tx.webhook_envoye,
            'solde_nonvipay_apres': str(tx.solde_nonvipay_apres),
            'date': tx.created_at.isoformat(),
        })


class ResetSoldesTestView(ApiKeyMixin, APIView):
    """
    POST /walletx/api/reset-soldes/

    Remet TOUS les comptes de test à leurs valeurs initiales.
    UNIQUEMENT en mode DEBUG.

    Après reset :
      CompteNonviPay(MTN_BEN).solde  = 0
      CompteNonviPay(MOOV_BEN).solde = 0
      Tous les comptes utilisateurs   = 500 000 FCFA
    """
    def post(self, request):
        if not settings.DEBUG:
            return Response(
                {'success': False, 'message': 'Non disponible en production.'},
                status=status.HTTP_403_FORBIDDEN
            )

        resultat = reset_soldes_test()
        return Response(resultat)


# ── Helper de conversion URL → code opérateur ─────────────────────────────────

def _url_vers_operateur(operateur_url: str) -> str:
    """
    Convertit le segment URL en code opérateur interne.

    'mtn'  → 'MTN_BEN'
    'moov' → 'MOOV_BEN'

    On utilise un mapping explicite plutôt qu'un simple .upper()
    pour rester extensible (ex: 'orange' → 'ORANGE_CI').
    """
    MAPPING = {
        'mtn':  'MTN_BEN',
        'moov': 'MOOV_BEN',
    }
    return MAPPING.get(operateur_url.lower(), operateur_url.upper() + '_BEN')