# apps/core/services.py — WalletX (VERSION MULTI-OPÉRATEURS)
"""
Service WalletX — Simule deux opérateurs Mobile Money distincts.

Chaque opérateur a :
  - Son propre compte NonviPay (ex: CompteNonviPay pour MTN_BEN)
  - Ses propres comptes utilisateurs (ex: +22997xxx pour MTN)
  - Ses propres endpoints (/depot/mtn/, /depot/moov/)

Le flux financier est identique pour les deux opérateurs,
seul le compte NonviPay et les utilisateurs changent.

Côté NonviPay, cela correspond à deux wallets GATEWAY distincts :
  GATEWAY_MTN  ←→ CompteNonviPay(MTN_BEN).solde
  GATEWAY_MOOV ←→ CompteNonviPay(MOOV_BEN).solde

Pourquoi séparer les GATEWAY ?
  Si MTN tombe en panne, les dépôts/retraits Moov continuent.
  La comptabilité reste claire : on sait exactement combien
  NonviPay possède chez chaque opérateur.
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


# ── Helpers communs ────────────────────────────────────────────────────────────

def _generer_reference_walletx(operateur: str) -> str:
    """
    Génère une référence unique contenant le code opérateur.
    Ex: WX-MTN-A3F9K2B7, WX-MOOV-X1Y2Z3W4
    """
    # Préfixe court de l'opérateur (MTN ou MOOV)
    prefixe = operateur.split('_')[0]  # 'MTN_BEN' → 'MTN', 'MOOV_BEN' → 'MOOV'
    return f"WX-{prefixe}-{uuid.uuid4().hex[:8].upper()}"


def get_ou_creer_compte_utilisateur(numero: str, operateur: str) -> CompteUtilisateur:
    """
    Récupère ou crée le compte d'un utilisateur pour un opérateur donné.

    Un utilisateur peut avoir un compte MTN ET un compte Moov :
      get_ou_creer_compte_utilisateur('+22997000001', 'MTN_BEN')  → compte MTN
      get_ou_creer_compte_utilisateur('+22961000001', 'MOOV_BEN') → compte Moov

    Le solde de départ est 500 000 FCFA pour faciliter les tests.
    """
    numero = numero.strip().replace(' ', '')
    compte, created = CompteUtilisateur.objects.get_or_create(
        numero_telephone=numero,
        operateur=operateur,
        defaults={
            'nom_titulaire': f'Titulaire {numero[-4:]} ({operateur})',
            'solde': Decimal('500000'),  # 500k FCFA pour les tests
            'est_actif': True,
        }
    )
    if created:
        logger.info(f"[WALLETX] Nouveau compte {operateur} créé : {numero}")
    return compte


# ══════════════════════════════════════════════════════════════════════════════
# DÉPÔT (générique — utilisé par MTN et Moov)
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def initier_depot(
    operateur: str,
    numero: str,
    montant: Decimal,
    reference_externe: str,
    webhook_url: str,
    description: str = '',
) -> dict:
    """
    Dépôt : L'utilisateur envoie de l'argent vers son compte NonviPay.

    Mouvement WalletX (pour l'opérateur donné) :
      CompteUtilisateur(operateur, numero).solde  -montant
      CompteNonviPay(operateur).solde             +montant

    Puis webhook SUCCESS → NonviPay crédite GATEWAY_<OPERATEUR> + USER_WALLET.

    Args:
        operateur        : 'MTN_BEN' ou 'MOOV_BEN'
        numero           : Numéro de téléphone de l'utilisateur
        montant          : Montant en FCFA
        reference_externe: Référence unique NonviPay (idempotence)
        webhook_url      : URL du webhook NonviPay
        description      : Description optionnelle
    """
    montant = Decimal(str(montant))

    # ── Idempotence : si la transaction existe déjà, ne pas la recréer ────────
    # Un même reference_externe ne doit jamais générer deux mouvements.
    existant = TransactionWalletX.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        logger.info(
            f"[WALLETX {operateur}] Dépôt idempotent : {reference_externe} "
            f"(statut: {existant.statut})"
        )
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée (idempotente).',
            'idempotent': True,
        }

    # ── Récupérer les comptes ──────────────────────────────────────────────────
    compte_user     = get_ou_creer_compte_utilisateur(numero, operateur)
    compte_nonvipay = CompteNonviPay.get_instance(operateur)

    # ── Validations ────────────────────────────────────────────────────────────
    if not compte_user.est_actif:
        return {
            'statut': 'FAILED',
            'message': f'Compte {operateur} désactivé pour ce numéro.',
            'code': 'ACCOUNT_DISABLED',
        }

    if montant <= 0:
        return {'statut': 'FAILED', 'message': 'Montant invalide.', 'code': 'INVALID_AMOUNT'}

    if compte_user.solde < montant:
        return {
            'statut': 'FAILED',
            'message': (
                f'Solde {operateur} insuffisant. '
                f'Disponible : {compte_user.solde} FCFA, '
                f'Demandé : {montant} FCFA.'
            ),
            'code': 'INSUFFICIENT_FUNDS',
        }

    # ── Verrouillage des deux comptes (évite les race conditions) ─────────────
    compte_user_lock     = CompteUtilisateur.objects.select_for_update().get(id=compte_user.id)
    compte_nonvipay_lock = CompteNonviPay.objects.select_for_update().get(id=compte_nonvipay.id)

    # Snapshots avant mouvement (pour l'audit trail)
    solde_user_avant     = compte_user_lock.solde
    solde_nonvipay_avant = compte_nonvipay_lock.solde

    # ── Mouvement financier ────────────────────────────────────────────────────
    # L'utilisateur perd de l'argent chez son opérateur
    compte_user_lock.solde -= montant
    compte_user_lock.save(update_fields=['solde', 'updated_at'])

    # NonviPay reçoit cet argent chez cet opérateur
    compte_nonvipay_lock.solde += montant
    compte_nonvipay_lock.save(update_fields=['solde', 'updated_at'])

    # ── Créer la trace de la transaction ──────────────────────────────────────
    reference_walletx = _generer_reference_walletx(operateur)

    tx = TransactionWalletX.objects.create(
        compte_utilisateur=compte_user_lock,
        operateur=operateur,
        solde_user_avant=solde_user_avant,
        solde_user_apres=compte_user_lock.solde,
        solde_nonvipay_avant=solde_nonvipay_avant,
        solde_nonvipay_apres=compte_nonvipay_lock.solde,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='DEPOT',
        montant=montant,
        statut='SUCCESS',
        description=description or f'Dépôt {operateur} vers NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX {operateur} DÉPÔT] {numero} : "
        f"{solde_user_avant} → {compte_user_lock.solde} FCFA | "
        f"NonviPay({operateur}) : {solde_nonvipay_avant} → {compte_nonvipay_lock.solde} FCFA | "
        f"Réf: {reference_walletx}"
    )

    # ── Envoyer le webhook à NonviPay ──────────────────────────────────────────
    # NonviPay va créer les écritures comptables (GATEWAY + USER_WALLET) après ce webhook
    _envoyer_webhook(tx, operateur)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'operateur': operateur,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'solde_restant': str(compte_user_lock.solde),
        'solde_nonvipay': str(compte_nonvipay_lock.solde),
        'message': f'Dépôt {operateur} de {montant} FCFA confirmé.',
    }


# ══════════════════════════════════════════════════════════════════════════════
# RETRAIT (générique — utilisé par MTN et Moov)
# ══════════════════════════════════════════════════════════════════════════════

@transaction.atomic
def initier_retrait(
    operateur: str,
    numero: str,
    montant: Decimal,
    reference_externe: str,
    webhook_url: str,
    description: str = '',
) -> dict:
    """
    Retrait : NonviPay envoie de l'argent vers l'utilisateur.

    Mouvement WalletX :
      CompteNonviPay(operateur).solde             -montant
      CompteUtilisateur(operateur, numero).solde  +montant

    Puis webhook SUCCESS → NonviPay finalise les écritures comptables
    (USER_WALLET -montant_total, GATEWAY -montant_net, FEE +frais).

    IMPORTANT : Les frais sont gérés CÔTÉ NonviPay, pas ici.
    WalletX reçoit uniquement le montant NET à verser à l'utilisateur.

    Args:
        operateur        : 'MTN_BEN' ou 'MOOV_BEN'
        numero           : Numéro de téléphone destinataire
        montant          : Montant NET à envoyer (sans frais)
        reference_externe: Référence unique NonviPay
        webhook_url      : URL du webhook NonviPay
        description      : Description optionnelle
    """
    montant = Decimal(str(montant))

    # ── Idempotence ────────────────────────────────────────────────────────────
    existant = TransactionWalletX.objects.filter(
        reference_externe=reference_externe
    ).first()
    if existant:
        logger.info(
            f"[WALLETX {operateur}] Retrait idempotent : {reference_externe}"
        )
        return {
            'reference_walletx': existant.reference_walletx,
            'reference_externe': reference_externe,
            'statut': existant.statut,
            'message': 'Transaction déjà traitée (idempotente).',
            'idempotent': True,
        }

    compte_user     = get_ou_creer_compte_utilisateur(numero, operateur)
    compte_nonvipay = CompteNonviPay.get_instance(operateur)

    # ── Validations ────────────────────────────────────────────────────────────
    if not compte_user.est_actif:
        return {
            'statut': 'FAILED',
            'message': f'Compte {operateur} désactivé pour ce numéro.',
            'code': 'ACCOUNT_DISABLED',
        }

    if montant <= 0:
        return {'statut': 'FAILED', 'message': 'Montant invalide.', 'code': 'INVALID_AMOUNT'}

    # Vérifier que NonviPay a assez d'argent chez CET opérateur
    if compte_nonvipay.solde < montant:
        return {
            'statut': 'FAILED',
            'message': (
                f'Solde NonviPay insuffisant chez {operateur}. '
                f'Disponible : {compte_nonvipay.solde} FCFA, '
                f'Demandé : {montant} FCFA.'
            ),
            'code': 'NONVIPAY_INSUFFICIENT_FUNDS',
        }

    # ── Verrouillage ──────────────────────────────────────────────────────────
    compte_user_lock     = CompteUtilisateur.objects.select_for_update().get(id=compte_user.id)
    compte_nonvipay_lock = CompteNonviPay.objects.select_for_update().get(id=compte_nonvipay.id)

    solde_user_avant     = compte_user_lock.solde
    solde_nonvipay_avant = compte_nonvipay_lock.solde

    # ── Mouvement financier ────────────────────────────────────────────────────
    # NonviPay envoie de l'argent → son solde chez l'opérateur diminue
    compte_nonvipay_lock.solde -= montant
    compte_nonvipay_lock.save(update_fields=['solde', 'updated_at'])

    # L'utilisateur reçoit l'argent sur son compte opérateur
    compte_user_lock.solde += montant
    compte_user_lock.save(update_fields=['solde', 'updated_at'])

    # ── Trace ──────────────────────────────────────────────────────────────────
    reference_walletx = _generer_reference_walletx(operateur)

    tx = TransactionWalletX.objects.create(
        compte_utilisateur=compte_user_lock,
        operateur=operateur,
        solde_user_avant=solde_user_avant,
        solde_user_apres=compte_user_lock.solde,
        solde_nonvipay_avant=solde_nonvipay_avant,
        solde_nonvipay_apres=compte_nonvipay_lock.solde,
        reference_externe=reference_externe,
        reference_walletx=reference_walletx,
        sens='RETRAIT',
        montant=montant,
        statut='SUCCESS',
        description=description or f'Retrait {operateur} depuis NonviPay — {reference_externe}',
        webhook_url=webhook_url,
    )

    logger.info(
        f"[WALLETX {operateur} RETRAIT] {numero} : "
        f"{solde_user_avant} → {compte_user_lock.solde} FCFA | "
        f"NonviPay({operateur}) : {solde_nonvipay_avant} → {compte_nonvipay_lock.solde} FCFA | "
        f"Réf: {reference_walletx}"
    )

    _envoyer_webhook(tx, operateur)

    return {
        'reference_walletx': reference_walletx,
        'reference_externe': reference_externe,
        'operateur': operateur,
        'statut': 'SUCCESS',
        'montant': str(montant),
        'nouveau_solde_user': str(compte_user_lock.solde),
        'solde_nonvipay': str(compte_nonvipay_lock.solde),
        'message': f'Retrait {operateur} de {montant} FCFA confirmé.',
    }


# ══════════════════════════════════════════════════════════════════════════════
# CONSULTATION & OUTILS
# ══════════════════════════════════════════════════════════════════════════════

def consulter_solde_utilisateur(numero: str, operateur: str) -> dict:
    """Retourne le solde d'un compte utilisateur sur un opérateur."""
    compte = get_ou_creer_compte_utilisateur(numero, operateur)
    return {
        'numero': compte.numero_telephone,
        'operateur': operateur,
        'nom': compte.nom_titulaire,
        'solde': str(compte.solde),
        'est_actif': compte.est_actif,
    }


def consulter_solde_nonvipay(operateur: str) -> dict:
    """
    Retourne le solde du compte NonviPay chez un opérateur.
    Ce solde doit correspondre au wallet GATEWAY_EXTERNAL correspondant dans NonviPay.
    """
    compte = CompteNonviPay.get_instance(operateur)
    return {
        'operateur': operateur,
        'nom': compte.nom,
        'solde': str(compte.solde),
        'est_actif': compte.est_actif,
        'info': (
            f"Ce solde doit correspondre à "
            f"NonviPay.GATEWAY_{operateur.replace('_BEN', '')}.available_balanced"
        ),
    }


def consulter_tous_soldes_nonvipay() -> dict:
    """Retourne les soldes NonviPay pour TOUS les opérateurs."""
    from apps.core.models import OPERATEUR_CHOICES
    soldes = {}
    for code, libelle in OPERATEUR_CHOICES:
        compte = CompteNonviPay.get_instance(code)
        soldes[code] = {
            'libelle': libelle,
            'solde': str(compte.solde),
            'est_actif': compte.est_actif,
        }
    return {
        'nonvipay_soldes': soldes,
        'total': str(sum(
            compte.solde
            for compte in CompteNonviPay.objects.filter(nom='NonviPay')
        )),
        'info': "Le total représente l'argent réel de NonviPay chez tous les opérateurs",
    }


def crediter_compte_utilisateur(
    numero: str,
    montant: Decimal,
    operateur: str,
) -> dict:
    """Recharge manuelle d'un compte utilisateur (tests uniquement)."""
    compte = get_ou_creer_compte_utilisateur(numero, operateur)
    solde_avant = compte.solde
    compte.solde += Decimal(str(montant))
    compte.save(update_fields=['solde', 'updated_at'])
    logger.info(
        f"[WALLETX] Recharge {operateur} : {numero} +{montant} | "
        f"{solde_avant} → {compte.solde}"
    )
    return {
        'numero': numero,
        'operateur': operateur,
        'solde_avant': str(solde_avant),
        'montant_credite': str(montant),
        'nouveau_solde': str(compte.solde),
    }


def reset_soldes_test() -> dict:
    """
    Remet tous les comptes de test à leurs valeurs initiales.
    UNIQUEMENT en DEBUG — jamais en production.

    Comptes MTN remis à 500 000 FCFA :
      +22997000001 (Alice), +22997000002 (Kofi), +22997000003 (Ama)
    Comptes Moov remis à 500 000 FCFA :
      +22961000001 (Bob), +22961000002 (Cécile), +22961000003 (David)
    Comptes NonviPay remis à 0 :
      CompteNonviPay(MTN_BEN) = 0
      CompteNonviPay(MOOV_BEN) = 0
    """
    SOLDE_INITIAL = Decimal('500000')

    COMPTES_TEST = {
        'MTN_BEN': [
            ('+22997000001', 'Alice Koffi'),
            ('+22997000002', 'Kofi Mensah'),
            ('+22997000003', 'Ama Sossa'),
        ],
        'MOOV_BEN': [
            ('+22961000001', 'Bob Alabi'),
            ('+22961000002', 'Cécile Dossou'),
            ('+22961000003', 'David Azonhiho'),
        ],
    }

    remis = {'MTN_BEN': [], 'MOOV_BEN': []}

    # Reset des comptes NonviPay
    for operateur in ['MTN_BEN', 'MOOV_BEN']:
        compte_np = CompteNonviPay.get_instance(operateur)
        compte_np.solde = Decimal('0')
        compte_np.save(update_fields=['solde', 'updated_at'])

    # Reset des comptes utilisateurs
    for operateur, comptes in COMPTES_TEST.items():
        for numero, nom in comptes:
            compte, created = CompteUtilisateur.objects.get_or_create(
                numero_telephone=numero,
                operateur=operateur,
                defaults={'nom_titulaire': nom, 'est_actif': True}
            )
            ancien_solde = compte.solde
            compte.solde = SOLDE_INITIAL
            compte.est_actif = True
            compte.save(update_fields=['solde', 'est_actif', 'updated_at'])
            remis[operateur].append({
                'numero': numero,
                'nom': nom,
                'ancien_solde': str(ancien_solde),
                'nouveau_solde': str(SOLDE_INITIAL),
                'created': created,
            })

    return {
        'success': True,
        'message': 'Tous les soldes de test ont été réinitialisés.',
        'nonvipay_mtn': '0 FCFA',
        'nonvipay_moov': '0 FCFA',
        'comptes_mtn': remis['MTN_BEN'],
        'comptes_moov': remis['MOOV_BEN'],
    }


# ══════════════════════════════════════════════════════════════════════════════
# WEBHOOK INTERNE
# ══════════════════════════════════════════════════════════════════════════════

def _envoyer_webhook(tx: TransactionWalletX, operateur: str):
    """
    Envoie le webhook de confirmation à NonviPay.

    Le payload inclut l'opérateur pour que NonviPay sache quel
    wallet GATEWAY créditer/débiter.

    Ce webhook est non-bloquant : une erreur d'envoi ne fait pas
    échouer la transaction WalletX (l'argent a déjà bougé).
    """
    if not tx.webhook_url:
        return

    payload = {
        'event': 'TRANSACTION_CONFIRMED',
        'reference_externe': tx.reference_externe,
        'reference_walletx': tx.reference_walletx,
        'operateur': operateur,               # ← NOUVEAU : identifie le réseau
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
        tx.webhook_envoye    = True
        tx.webhook_response  = f"{response.status_code}: {response.text[:200]}"
        tx.webhook_envoye_le = timezone.now()
        tx.save(update_fields=[
            'webhook_envoye', 'webhook_response', 'webhook_envoye_le', 'updated_at'
        ])
        logger.info(
            f"[WALLETX {operateur}] Webhook → {tx.webhook_url} "
            f"| HTTP {response.status_code}"
        )

    except requests.exceptions.RequestException as e:
        tx.webhook_response = f"ERREUR: {str(e)[:200]}"
        tx.save(update_fields=['webhook_response', 'updated_at'])
        logger.error(f"[WALLETX {operateur}] Échec webhook : {e}")


def _signer_payload(payload: dict) -> str:
    """Signature HMAC-SHA256 pour sécuriser les webhooks envoyés à NonviPay."""
    secret = getattr(settings, 'WALLETX_WEBHOOK_SECRET', 'walletx-webhook-secret-2026')
    message = json.dumps(payload, sort_keys=True)
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return f"sha256={sig}"