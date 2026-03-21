# apps/core/templatetags/walletx_filters.py
"""
Filtres de template personnalisés pour le dashboard WalletX.

Usage dans le template :
  {% load walletx_filters %}
  {{ c.solde|div_by_max:comptes_mtn }}   → pourcentage pour la barre de progression
  {{ montant|fmt_fcfa }}                  → "10 000 FCFA"
"""
from django import template

register = template.Library()


@register.filter(name='div_by_max')
def div_by_max(value, comptes):
    """
    Calcule le pourcentage d'un solde par rapport au maximum de la liste.
    Utilisé pour calculer la largeur des barres de progression.

    Exemple :
      solde = 300 000, max = 500 000 → 60 (%)

    Si la liste est vide ou le max est 0, retourne 0.
    """
    try:
        max_solde = max(c['solde'] for c in comptes) if comptes else 0
        if max_solde == 0:
            return 0
        return round((float(value) / float(max_solde)) * 100)
    except (TypeError, ValueError, KeyError):
        return 0


@register.filter(name='fmt_fcfa')
def fmt_fcfa(value):
    """
    Formate un nombre en FCFA avec séparateur de milliers.
    Exemple : 500000 → "500 000 FCFA"
    """
    try:
        n = int(round(float(value)))
        return f"{n:,}".replace(',', '\u202f') + ' FCFA'
    except (TypeError, ValueError):
        return '0 FCFA'


@register.filter(name='sens_icon')
def sens_icon(sens):
    """Retourne l'icône correspondant au sens de la transaction."""
    return '↓' if sens == 'DEPOT' else '↑'


@register.filter(name='op_label')
def op_label(operateur):
    """Retourne le label court de l'opérateur."""
    return 'MTN' if operateur == 'MTN_BEN' else 'MOOV'
