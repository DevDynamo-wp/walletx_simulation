"""
apps/core/services.py — Logique métier WalletX

Simule exactement le comportement d'un opérateur Mobile Money :

  initier_depot()  :  NonviPay appelle WalletX pour un dépôt
                      → WalletX DÉBITE le compte virtuel (argent qui part du téléphone)
                      → WalletX envoie un webhook SUCCESS à NonviPay
                      → NonviPay crédite le wallet de l'utilisateur

  initier_retrait():  NonviPay appelle WalletX pour un retrait
                      → WalletX CRÉDITE le compte virtuel (argent qui arrive sur le téléphone)
                      → WalletX envoie un webhook SUCCESS à NonviPay
                      → NonviPay confirme la finalisation
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

from apps.core.models import CompteVirtuel, TransactionVirtuelle

logger = logging.getLogger(__name__)


def _generer_reference_walletx() -> str:
    return f"WX-{uuid.uuid4().hex[:12].upper()}"


def get_ou_creer_compte(numero: str) -> CompteVirtuel:
    """
    Récupère ou crée un compte virtuel pour ce numéro.
    Solde de départ : 100 000 FCFA.
    """
    numero = numero.strip().replace(' ', '')
    compte, created = CompteVirtuel.objects.get_or_create(
        numero_telephone=numero,
        defaults={
            'nom_titulaire': f'Titulaire {numero[-4:]}',
            'solde': Decimal('100000'),
            'est_actif': True,
        }
    )
    if created:
        logger.info(f"[WALLETX] Nouveau compte virtuel créé : {numero}")
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
    Dépôt : débite le compte virtuel, envoie webhook SUCCESS à NonviPay.
    """
    montant = Decimal(str(montant))

    # Idempotence — même reference_externe → même résultat
    existant = TransactionVirtuelle.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée.',
            'idempotent': True,
        }

    compte = get_ou_creer_compte(numero)

    if not compte.est_actif:
        return {
            'statut': 'FAILED',
            'message': 'Ce compte est désactivé.',
            'code': 'ACCOUNT_DISABLED',
        }

    if montant <= 0:
        return {
            'statut': 'FAILED',
            'message': 'Montant invalide.',
            'code': 'INVALID_AMOUNT',
        }

    if compte.solde < montant:
        return {
            'statut': 'FAILED',
            'message': f'Solde insuffisant. Disponible : {compte.solde} FCFA.',
            'code': 'INSUFFICIENT_FUNDS',
        }

    # Débiter le compte virtuel
    solde_avant = compte.solde
    compte.solde -= montant
    compte.save(update_fields=['solde', 'updated_at'])

    reference_walletx = _generer_reference_walletx()

    tx = TransactionVirtuelle.objects.create(
        compte=compte,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='DEBIT',
        montant=montant,
        solde_avant=solde_avant,
        solde_apres=compte.solde,
        statut='SUCCESS',
        description=description or f'Dépôt vers NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX] DÉPÔT : {numero} débité de {montant} FCFA | "
        f"{solde_avant} → {compte.solde} | Réf : {reference_walletx}"
    )

    # Envoyer le webhook à NonviPay
    _envoyer_webhook(tx)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'solde_restant': str(compte.solde),
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
    Retrait : crédite le compte virtuel, envoie webhook SUCCESS à NonviPay.
    """
    montant = Decimal(str(montant))

    # Idempotence
    existant = TransactionVirtuelle.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée.',
            'idempotent': True,
        }

    compte = get_ou_creer_compte(numero)

    if not compte.est_actif:
        return {
            'statut': 'FAILED',
            'message': 'Ce compte est désactivé.',
            'code': 'ACCOUNT_DISABLED',
        }

    if montant <= 0:
        return {
            'statut': 'FAILED',
            'message': 'Montant invalide.',
            'code': 'INVALID_AMOUNT',
        }

    # Créditer le compte virtuel
    solde_avant = compte.solde
    compte.solde += montant
    compte.save(update_fields=['solde', 'updated_at'])

    reference_walletx = _generer_reference_walletx()

    tx = TransactionVirtuelle.objects.create(
        compte=compte,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='CREDIT',
        montant=montant,
        solde_avant=solde_avant,
        solde_apres=compte.solde,
        statut='SUCCESS',
        description=description or f'Retrait depuis NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX] RETRAIT : {numero} crédité de {montant} FCFA | "
        f"{solde_avant} → {compte.solde} | Réf : {reference_walletx}"
    )

    _envoyer_webhook(tx)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'nouveau_solde': str(compte.solde),
        'message': f'Retrait de {montant} FCFA confirmé.',
    }


def consulter_solde(numero: str) -> dict:
    """Retourne le solde d'un compte virtuel."""
    compte = get_ou_creer_compte(numero)
    return {
        'numero': compte.numero_telephone,
        'nom': compte.nom_titulaire,
        'solde': str(compte.solde),
        'est_actif': compte.est_actif,
    }


def crediter_compte(numero: str, montant: Decimal) -> dict:
    """Recharge manuelle d'un compte virtuel (pour les tests)."""
    compte = get_ou_creer_compte(numero)
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


def _envoyer_webhook(tx: TransactionVirtuelle):
    """
    Envoie le webhook de confirmation à NonviPay.
    Non bloquant : une erreur de webhook n'annule pas la transaction.
    """
    if not tx.webhook_url:
        return

    payload = {
        'event': 'TRANSACTION_CONFIRMED',
        'reference_externe': tx.reference_externe,
        'reference_walletx': tx.reference_walletx,
        'numero_telephone': tx.compte.numero_telephone,
        'montant': str(tx.montant),
        'sens': tx.sens,
        'statut': tx.statut,
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
        tx.webhook_envoye = True
        tx.webhook_response = f"{response.status_code}: {response.text[:200]}"
        tx.webhook_envoye_le = timezone.now()
        tx.save(update_fields=[
            'webhook_envoye', 'webhook_response', 'webhook_envoye_le', 'updated_at'
        ])
        logger.info(f"[WALLETX] Webhook envoyé → {tx.webhook_url} | {response.status_code}")

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