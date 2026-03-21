// apps/core/static/core/js/dashboard.js
// Logique front-end du dashboard WalletX.
//
// Responsabilités :
//   1. Utiliser les données initiales injectées par Django (DONNEES_INITIALES)
//   2. Rafraîchir les données toutes les 5s via fetch(URL_DATA)
//   3. Rendre les tableaux et le journal
//   4. Appeler l'API WalletX pour les simulations dépôt/retrait
//   5. Afficher des toasts de feedback
//
// Ce fichier suppose que les variables globales suivantes sont définies
// dans le template AVANT le chargement de ce script :
//   DONNEES_INITIALES, URL_DATA, URL_DEPOT, URL_RETRAIT, URL_RESET, API_KEY

// ── État courant ────────────────────────────────────────────────────────────
let etat = { ...DONNEES_INITIALES };

// ── Formateurs ──────────────────────────────────────────────────────────────
function fmt(n) {
  return Math.round(Number(n)).toLocaleString('fr-FR') + ' FCFA';
}
function opClass(op) { return op === 'MTN_BEN' ? 'mtn' : 'moov'; }
function opLabel(op) { return op === 'MTN_BEN' ? 'MTN' : 'MOOV'; }
function opUrl(op)   { return op === 'MTN_BEN' ? 'mtn' : 'moov'; }

// ── Rendu table ─────────────────────────────────────────────────────────────
function renderTable(comptes, tbodyId, cls) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;

  const maxSolde = Math.max(...comptes.map(c => c.solde), 1);

  tbody.innerHTML = comptes.map(c => {
    const pct      = Math.round((c.solde / maxSolde) * 100);
    const sldClass = c.solde < 50000 ? 'low' : (c.solde >= 400000 ? 'high' : '');
    return `
      <tr>
        <td class="td-nom">${c.nom}</td>
        <td class="td-num">${c.numero}</td>
        <td class="td-solde ${sldClass}">${fmt(c.solde)}</td>
        <td class="td-bar">
          <div class="bar-track">
            <div class="bar-fill ${cls}" style="width:${pct}%"></div>
          </div>
        </td>
        <td>
          <span class="pill ${c.est_actif ? 'actif' : 'inactif'}">
            ${c.est_actif ? 'actif' : 'inactif'}
          </span>
        </td>
      </tr>`;
  }).join('');
}

// ── Rendu journal ───────────────────────────────────────────────────────────
function renderJournal(journal) {
  const el = document.getElementById('journal');
  if (!el) return;

  if (!journal || journal.length === 0) {
    el.innerHTML = '<div class="empty-state">Aucune transaction — utilisez le formulaire ci-dessus.</div>';
    return;
  }

  el.innerHTML = journal.map(tx => {
    const sens    = tx.sens === 'DEPOT' ? 'depot' : 'retrait';
    const icone   = tx.sens === 'DEPOT' ? '↓' : '↑';
    const signe   = tx.sens === 'DEPOT' ? '+' : '−';
    const opCls   = opClass(tx.operateur);
    const opLbl   = opLabel(tx.operateur);
    return `
      <div class="journal-item">
        <div class="ji-icon ${sens}">${icone}</div>
        <div class="ji-body">
          <div class="desc">
            ${tx.nom}
            <span class="op-tag ${opCls}">${opLbl}</span>
          </div>
          <div class="ref">${tx.reference_walletx} · ${tx.heure}</div>
        </div>
        <div class="ji-amount">
          <div class="montant ${sens}">${signe}${fmt(tx.montant)}</div>
          <div class="time">NP: ${fmt(tx.solde_np_apres)}</div>
        </div>
      </div>`;
  }).join('');
}

// ── Rendu métriques ─────────────────────────────────────────────────────────
function renderStats(stats) {
  setVal('total-np',   fmt(stats.total_nonvipay));
  setVal('solde-mtn',  fmt(stats.solde_mtn));
  setVal('solde-moov', fmt(stats.solde_moov));
  setVal('gw-mtn',     fmt(stats.solde_mtn));
  setVal('gw-moov',    fmt(stats.solde_moov));
  setVal('tx-jour',    stats.nb_aujourd_hui);
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ── Flash animation ─────────────────────────────────────────────────────────
function flash(id, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('flash', 'flash-down');
  el.classList.add(type === 'up' ? 'flash' : 'flash-down');
  setTimeout(() => el.classList.remove('flash', 'flash-down'), 700);
}

// ── Toast ───────────────────────────────────────────────────────────────────
function toast(msg, type = 'success') {
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3500);
}

// ── Sélecteurs de comptes ────────────────────────────────────────────────────
function majComptes(opSelectId, numSelectId) {
  const op      = document.getElementById(opSelectId)?.value;
  const sel     = document.getElementById(numSelectId);
  if (!sel || !op) return;

  const comptes = op === 'MTN_BEN' ? etat.comptesMtn : etat.comptesMoov;
  sel.innerHTML = comptes.map(c =>
    `<option value="${c.numero}">${c.nom} — ${fmt(c.solde)}</option>`
  ).join('');
}

function majComptesDepot()   { majComptes('dep-op', 'dep-numero'); }
function majComptesRetrait() { majComptes('ret-op', 'ret-numero'); }

// ── Render complet ──────────────────────────────────────────────────────────
function renderAll() {
  renderStats(etat.stats);
  renderTable(etat.comptesMtn,  'tbody-mtn',  'mtn');
  renderTable(etat.comptesMoov, 'tbody-moov', 'moov');
  renderJournal(etat.journal);
  majComptesDepot();
  majComptesRetrait();
}

// ── Polling (fetch toutes les 5 secondes) ────────────────────────────────────
const POLL_INTERVAL = 5000;  // ms
let   pollTimer     = null;
let   pollStart     = Date.now();

function demarrerPolling() {
  const bar = document.getElementById('refresh-bar');

  function progresser() {
    const elapsed = Date.now() - pollStart;
    const pct     = Math.min((elapsed / POLL_INTERVAL) * 100, 100);
    if (bar) bar.style.width = pct + '%';
    if (elapsed < POLL_INTERVAL) {
      requestAnimationFrame(progresser);
    }
  }

  async function fetchData() {
    try {
      const res  = await fetch(URL_DATA, { headers: { 'Accept': 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      // Détecter les changements de solde pour les animations
      const prevMtn  = etat.stats.solde_mtn;
      const prevMoov = etat.stats.solde_moov;

      etat.stats       = data.stats;
      etat.comptesMtn  = data.comptes_mtn;
      etat.comptesMoov = data.comptes_moov;
      etat.journal     = data.journal;

      renderAll();

      if (data.stats.solde_mtn  > prevMtn)  flash('gw-mtn',  'up');
      if (data.stats.solde_mtn  < prevMtn)  flash('gw-mtn',  'down');
      if (data.stats.solde_moov > prevMoov) flash('gw-moov', 'up');
      if (data.stats.solde_moov < prevMoov) flash('gw-moov', 'down');

    } catch (err) {
      console.warn('[WalletX Dashboard] Erreur polling :', err.message);
    } finally {
      // Relancer le cycle
      pollStart = Date.now();
      if (bar) bar.style.width = '0%';
      requestAnimationFrame(progresser);
      pollTimer = setTimeout(fetchData, POLL_INTERVAL);
    }
  }

  // Premier cycle
  pollStart = Date.now();
  requestAnimationFrame(progresser);
  pollTimer = setTimeout(fetchData, POLL_INTERVAL);
}

// ── Simulation dépôt ─────────────────────────────────────────────────────────
async function simulerDepot() {
  const op      = document.getElementById('dep-op')?.value;
  const numero  = document.getElementById('dep-numero')?.value;
  const montant = document.getElementById('dep-montant')?.value;

  if (!numero)  { toast('Sélectionnez un compte.', 'error'); return; }
  if (!montant || Number(montant) <= 0) { toast('Montant invalide.', 'error'); return; }

  const urlBase = URL_DEPOT.replace('{op}', opUrl(op));
  const ref     = 'SIM-' + Date.now();

  try {
    const res  = await fetch(urlBase, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({
        numero_telephone: numero,
        montant:          montant,
        reference_externe: ref,
        webhook_url:      '',
        description:      'Simulation dashboard',
      }),
    });

    const data = await res.json();

    if (data.success) {
      toast(`Dépôt ${fmt(montant)} confirmé — NonviPay: ${fmt(data.solde_nonvipay)}`);
      flash(op === 'MTN_BEN' ? 'gw-mtn' : 'gw-moov', 'up');
      // Forcer un refresh immédiat
      clearTimeout(pollTimer);
      pollStart = Date.now();
      const freshRes  = await fetch(URL_DATA);
      const freshData = await freshRes.json();
      etat.stats       = freshData.stats;
      etat.comptesMtn  = freshData.comptes_mtn;
      etat.comptesMoov = freshData.comptes_moov;
      etat.journal     = freshData.journal;
      renderAll();
      demarrerPolling();
    } else {
      toast(data.message || 'Erreur lors du dépôt.', 'error');
    }
  } catch (err) {
    toast('Impossible de contacter l\'API WalletX.', 'error');
    console.error(err);
  }
}

// ── Simulation retrait ───────────────────────────────────────────────────────
async function simulerRetrait() {
  const op      = document.getElementById('ret-op')?.value;
  const numero  = document.getElementById('ret-numero')?.value;
  const montant = document.getElementById('ret-montant')?.value;
  const frais   = document.getElementById('ret-frais')?.value || '0';

  if (!numero)  { toast('Sélectionnez un compte.', 'error'); return; }
  if (!montant || Number(montant) <= 0) { toast('Montant invalide.', 'error'); return; }

  const urlBase = URL_RETRAIT.replace('{op}', opUrl(op));
  const ref     = 'SIM-' + Date.now();

  try {
    const res  = await fetch(urlBase, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({
        numero_telephone:  numero,
        montant:           montant,
        reference_externe: ref,
        webhook_url:       '',
        description:       `Simulation dashboard — frais: ${frais} FCFA`,
      }),
    });

    const data = await res.json();

    if (data.success) {
      const total = Number(montant) + Number(frais);
      toast(`Retrait ${fmt(montant)} confirmé${Number(frais) > 0 ? ` + ${fmt(frais)} frais` : ''}`);
      flash(op === 'MTN_BEN' ? 'gw-mtn' : 'gw-moov', 'down');
      clearTimeout(pollTimer);
      const freshRes  = await fetch(URL_DATA);
      const freshData = await freshRes.json();
      etat.stats       = freshData.stats;
      etat.comptesMtn  = freshData.comptes_mtn;
      etat.comptesMoov = freshData.comptes_moov;
      etat.journal     = freshData.journal;
      renderAll();
      demarrerPolling();
    } else {
      toast(data.message || 'Erreur lors du retrait.', 'error');
    }
  } catch (err) {
    toast('Impossible de contacter l\'API WalletX.', 'error');
    console.error(err);
  }
}

// ── Reset complet ────────────────────────────────────────────────────────────
async function resetSoldes() {
  if (!confirm('Remettre tous les soldes à leur valeur initiale ?')) return;
  try {
    const res  = await fetch(URL_RESET, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    });
    const data = await res.json();
    if (data.success) {
      toast('Soldes réinitialisés.', 'info');
      clearTimeout(pollTimer);
      const freshRes  = await fetch(URL_DATA);
      const freshData = await freshRes.json();
      etat.stats       = freshData.stats;
      etat.comptesMtn  = freshData.comptes_mtn;
      etat.comptesMoov = freshData.comptes_moov;
      etat.journal     = freshData.journal;
      renderAll();
      demarrerPolling();
    }
  } catch (err) {
    toast('Erreur lors du reset.', 'error');
  }
}

// ── Initialisation ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Les données initiales viennent de Django (déjà dans DONNEES_INITIALES).
  // On rend le JS sans fetch() supplémentaire.
  renderAll();

  // Écouter les changements d'opérateur
  document.getElementById('dep-op')?.addEventListener('change', majComptesDepot);
  document.getElementById('ret-op')?.addEventListener('change', majComptesRetrait);

  // Démarrer le polling
  demarrerPolling();

  console.log('[WalletX Dashboard] Démarré — polling toutes les 5s sur', URL_DATA);
});
