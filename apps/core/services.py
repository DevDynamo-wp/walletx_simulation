# apps/core/services.py — WalletX
"""
Service WalletX — Simule un opérateur Mobile Money réel.

Architecture des comptes :
  CompteNonviPay    : Compte de l'application NonviPay (1 seul, singleton)
  CompteUtilisateur : Comptes individuels par numéro de téléphone

DÉPÔT (vu de WalletX) :
  L'utilisateur veut mettre de l'argent sur son compte NonviPay.
  → CompteUtilisateur.solde  -montant
  → CompteNonviPay.solde     +montant
  → Webhook SUCCESS envoyé à NonviPay

RETRAIT (vu de WalletX) :
  L'utilisateur veut récupérer son argent de NonviPay.
  → CompteNonviPay.solde     -montant
  → CompteUtilisateur.solde  +montant
  → Webhook SUCCESS envoyé à NonviPay

Invariants :
  Après chaque transaction : somme totale des soldes = constante
  (l'argent se déplace, ne se crée pas)
"""
import hmac
import hashlib
import json
import uuid
import logging
import requests

from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.models import CompteNonviPay, CompteUtilisateur, TransactionWalletX

logger = logging.getLogger(__name__)


def _generer_reference_walletx() -> str:
    return f"WX-{uuid.uuid4().hex[:12].upper()}"


def get_ou_creer_compte_utilisateur(numero: str) -> CompteUtilisateur:
    """
    Récupère ou crée le compte d'un utilisateur.
    Solde de départ : 100 000 FCFA (pour les tests).
    """
    numero = numero.strip().replace(' ', '')
    compte, created = CompteUtilisateur.objects.get_or_create(
        numero_telephone=numero,
        defaults={
            'nom_titulaire': f'Titulaire {numero[-4:]}',
            'solde': Decimal('100000'),
            'est_actif': True,
        }
    )
    if created:
        logger.info(f"[WALLETX] Nouveau compte utilisateur créé : {numero}")
    return compte


@transaction.atomic
def initier_depot(
    numero: str,
    montant: Decimal,
    reference_externe: str,
    webhook_url: str,
    description: str = '',
) -> dict:
    """
    Dépôt : L'utilisateur envoie de l'argent vers NonviPay.

    Mouvement WalletX :
      CompteUtilisateur.solde  -montant
      CompteNonviPay.solde     +montant

    Puis webhook SUCCESS → NonviPay crédite GATEWAY_EXTERNAL et USER_WALLET.
    """
    montant = Decimal(str(montant))

    # ── Idempotence ────────────────────────────────────────────────────────────
    existant = TransactionWalletX.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée (idempotente).',
            'idempotent': True,
        }

    # ── Récupérer les comptes ──────────────────────────────────────────────────
    compte_user     = get_ou_creer_compte_utilisateur(numero)
    compte_nonvipay = CompteNonviPay.get_instance()

    # ── Validations ────────────────────────────────────────────────────────────
    if not compte_user.est_actif:
        return {'statut': 'FAILED', 'message': 'Compte utilisateur désactivé.', 'code': 'ACCOUNT_DISABLED'}

    if montant <= 0:
        return {'statut': 'FAILED', 'message': 'Montant invalide.', 'code': 'INVALID_AMOUNT'}

    if compte_user.solde < montant:
        return {
            'statut': 'FAILED',
            'message': f'Solde insuffisant. Disponible : {compte_user.solde} FCFA.',
            'code': 'INSUFFICIENT_FUNDS',
        }

    # ── Verrouillage et mouvement ──────────────────────────────────────────────
    # Verrouiller les deux comptes pour éviter les race conditions
    compte_user_lock     = CompteUtilisateur.objects.select_for_update().get(id=compte_user.id)
    compte_nonvipay_lock = CompteNonviPay.objects.select_for_update().get(id=compte_nonvipay.id)

    solde_user_avant     = compte_user_lock.solde
    solde_nonvipay_avant = compte_nonvipay_lock.solde

    # CompteUtilisateur perd de l'argent
    compte_user_lock.solde -= montant
    compte_user_lock.save(update_fields=['solde', 'updated_at'])

    # CompteNonviPay gagne de l'argent
    compte_nonvipay_lock.solde += montant
    compte_nonvipay_lock.save(update_fields=['solde', 'updated_at'])

    reference_walletx = _generer_reference_walletx()

    tx = TransactionWalletX.objects.create(
        compte_utilisateur=compte_user_lock,
        solde_user_avant=solde_user_avant,
        solde_user_apres=compte_user_lock.solde,
        solde_nonvipay_avant=solde_nonvipay_avant,
        solde_nonvipay_apres=compte_nonvipay_lock.solde,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='DEPOT',
        montant=montant,
        statut='SUCCESS',
        description=description or f'Dépôt vers NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX DÉPÔT] {numero} : {solde_user_avant} → {compte_user_lock.solde} FCFA | "
        f"NonviPay : {solde_nonvipay_avant} → {compte_nonvipay_lock.solde} FCFA | "
        f"Réf: {reference_walletx}"
    )

    # ── Envoyer le webhook à NonviPay ──────────────────────────────────────────
    _envoyer_webhook(tx)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'solde_user_restant': str(compte_user_lock.solde),
        'solde_nonvipay': str(compte_nonvipay_lock.solde),
        'message': f'Dépôt de {montant} FCFA confirmé.',
    }


@transaction.atomic
def initier_retrait(
    numero: str,
    montant: Decimal,
    reference_externe: str,
    webhook_url: str,
    description: str = '',
) -> dict:
    """
    Retrait : NonviPay envoie de l'argent vers l'utilisateur.

    Mouvement WalletX :
      CompteNonviPay.solde     -montant
      CompteUtilisateur.solde  +montant

    Puis webhook SUCCESS → NonviPay finalise le retrait côté comptabilité.
    """
    montant = Decimal(str(montant))

    # ── Idempotence ────────────────────────────────────────────────────────────
    existant = TransactionWalletX.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée (idempotente).',
            'idempotent': True,
        }

    compte_user     = get_ou_creer_compte_utilisateur(numero)
    compte_nonvipay = CompteNonviPay.get_instance()

    if not compte_user.est_actif:
        return {'statut': 'FAILED', 'message': 'Compte utilisateur désactivé.', 'code': 'ACCOUNT_DISABLED'}

    if montant <= 0:
        return {'statut': 'FAILED', 'message': 'Montant invalide.', 'code': 'INVALID_AMOUNT'}

    # Vérifier que NonviPay a assez d'argent chez WalletX
    if compte_nonvipay.solde < montant:
        return {
            'statut': 'FAILED',
            'message': f'Solde NonviPay insuffisant chez WalletX. Disponible : {compte_nonvipay.solde} FCFA.',
            'code': 'NONVIPAY_INSUFFICIENT_FUNDS',
        }

    # ── Verrouillage et mouvement ──────────────────────────────────────────────
    compte_user_lock     = CompteUtilisateur.objects.select_for_update().get(id=compte_user.id)
    compte_nonvipay_lock = CompteNonviPay.objects.select_for_update().get(id=compte_nonvipay.id)

    solde_user_avant     = compte_user_lock.solde
    solde_nonvipay_avant = compte_nonvipay_lock.solde

    # CompteNonviPay perd de l'argent
    compte_nonvipay_lock.solde -= montant
    compte_nonvipay_lock.save(update_fields=['solde', 'updated_at'])

    # CompteUtilisateur reçoit de l'argent
    compte_user_lock.solde += montant
    compte_user_lock.save(update_fields=['solde', 'updated_at'])

    reference_walletx = _generer_reference_walletx()

    tx = TransactionWalletX.objects.create(
        compte_utilisateur=compte_user_lock,
        solde_user_avant=solde_user_avant,
        solde_user_apres=compte_user_lock.solde,
        solde_nonvipay_avant=solde_nonvipay_avant,
        solde_nonvipay_apres=compte_nonvipay_lock.solde,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='RETRAIT',
        montant=montant,
        statut='SUCCESS',
        description=description or f'Retrait depuis NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX RETRAIT] {numero} : {solde_user_avant} → {compte_user_lock.solde} FCFA | "
        f"NonviPay : {solde_nonvipay_avant} → {compte_nonvipay_lock.solde} FCFA | "
        f"Réf: {reference_walletx}"
    )

    _envoyer_webhook(tx)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'nouveau_solde_user': str(compte_user_lock.solde),
        'solde_nonvipay': str(compte_nonvipay_lock.solde),
        'message': f'Retrait de {montant} FCFA confirmé.',
    }


def consulter_solde_utilisateur(numero: str) -> dict:
    """Retourne le solde d'un compte utilisateur."""
    compte = get_ou_creer_compte_utilisateur(numero)
    return {
        'numero': compte.numero_telephone,
        'nom': compte.nom_titulaire,
        'solde': str(compte.solde),
        'est_actif': compte.est_actif,
    }


def consulter_solde_nonvipay() -> dict:
    """Retourne le solde du compte NonviPay chez WalletX."""
    compte = CompteNonviPay.get_instance()
    return {
        'nom': compte.nom,
        'solde': str(compte.solde),
        'est_actif': compte.est_actif,
        'info': (
            "Ce solde doit correspondre exactement à "
            "NonviPay.GATEWAY_EXTERNAL.available_balanced"
        ),
    }


def crediter_compte_utilisateur(numero: str, montant: Decimal) -> dict:
    """Recharge manuelle d'un compte utilisateur (tests uniquement)."""
    compte = get_ou_creer_compte_utilisateur(numero)
    solde_avant = compte.solde
    compte.solde += Decimal(str(montant))
    compte.save(update_fields=['solde', 'updated_at'])
    logger.info(f"[WALLETX] Recharge : {numero} +{montant} | {solde_avant} → {compte.solde}")
    return {
        'numero': numero,
        'solde_avant': str(solde_avant),
        'montant_credite': str(montant),
        'nouveau_solde': str(compte.solde),
    }


def _envoyer_webhook(tx: TransactionWalletX):
    """Envoie le webhook de confirmation à NonviPay (non bloquant)."""
    if not tx.webhook_url:
        return

    payload = {
        'event': 'TRANSACTION_CONFIRMED',
        'reference_externe': tx.reference_externe,
        'reference_walletx': tx.reference_walletx,
        'numero_telephone': tx.compte_utilisateur.numero_telephone,
        'montant': str(tx.montant),
        'sens': tx.sens,
        'statut': tx.statut,
        'solde_nonvipay_apres': str(tx.solde_nonvipay_apres),
        'timestamp': tx.updated_at.isoformat(),
    }

    try:
        response = requests.post(
            tx.webhook_url,
            json=payload,
            timeout=10,
            headers={
                'Content-Type': 'application/json',
                'X-WalletX-Signature': _signer_payload(payload),
            }
        )
        tx.webhook_envoye   = True
        tx.webhook_response = f"{response.status_code}: {response.text[:200]}"
        tx.webhook_envoye_le = timezone.now()
        tx.save(update_fields=['webhook_envoye', 'webhook_response', 'webhook_envoye_le', 'updated_at'])
        logger.info(f"[WALLETX] Webhook → {tx.webhook_url} | HTTP {response.status_code}")

    except requests.exceptions.RequestException as e:
        tx.webhook_response = f"ERREUR: {str(e)[:200]}"
        tx.save(update_fields=['webhook_response', 'updated_at'])
        logger.error(f"[WALLETX] Échec webhook : {e}")


def _signer_payload(payload: dict) -> str:
    """Signature HMAC-SHA256 pour sécuriser les webhooks."""
    secret = getattr(settings, 'WALLETX_WEBHOOK_SECRET', 'walletx-webhook-secret-2026')
    message = json.dumps(payload, sort_keys=True)
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"sha256={sig}"