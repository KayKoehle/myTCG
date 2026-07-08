import { cardArtTag, effectLabel, escapeHtml, typeLabel } from './helpers.js';

// A reusable card-stack popup. Two modes:
//   'select' - pick one card from a stack, then press a confirm button
//              (e.g. "Banish Achilles", "Move Enkidu").
//   'view'   - browse a stack of cards read-only (e.g. an underworld pile).
// Cards render collapsed (headline + type) and expand to full art + effect on
// tap, mirroring how stacks read on the board.
export function createCardStackPopup(ui) {
    let mode = 'view';
    let selectedOption = null;
    let selectedName = '';
    let confirmVerb = 'Confirm';
    let onConfirm = null;
    let onExtra = null;
    let onClose = null;

    function cardTile({ card, option }, index) {
        const cost = card.cost ?? '?';
        const power = (card.power !== null && card.power !== undefined) ? card.power : '?';
        const type = typeLabel(card);
        return `
            <div class="stackpop-card" data-option="${escapeHtml(String(option ?? index))}" data-name="${escapeHtml(card.name || '')}">
                <div class="card-headline">
                    <span class="stat-badge cost">${escapeHtml(String(cost))}</span>
                    <div class="card-title">${escapeHtml(card.name || 'Unknown')}</div>
                    <span class="stat-badge power">${escapeHtml(String(power))}</span>
                </div>
                ${type ? `<div class="card-type">${escapeHtml(type)}</div>` : ''}
                <div class="stackpop-media">${cardArtTag(card.name, 'stackpop-art')}</div>
                <div class="stackpop-effect tiny">${escapeHtml(effectLabel(card))}</div>
            </div>
        `;
    }

    function expandTile(tile, select) {
        ui.stackList.querySelectorAll('.stackpop-card').forEach((el) => {
            el.classList.remove('expanded');
            if (select) el.classList.remove('selected');
        });
        tile.classList.add('expanded');
        if (select) {
            tile.classList.add('selected');
            selectedOption = tile.dataset.option;
            selectedName = tile.dataset.name || '';
        }
        updateConfirm();
    }

    function updateConfirm() {
        if (mode !== 'select') return;
        const ready = selectedOption !== null;
        ui.stackConfirm.disabled = !ready;
        ui.stackConfirm.textContent = ready
            ? `${confirmVerb} ${selectedName}`.trim()
            : `Select a card to ${confirmVerb.toLowerCase()}`;
    }

    function renderExtras(extras) {
        ui.stackExtras.innerHTML = (extras || [])
            .map((opt) => `<button class="choice-option-btn stackpop-extra" data-value="${escapeHtml(String(opt.value))}">${escapeHtml(opt.label)}</button>`)
            .join('');
    }

    function open(opts) {
        mode = opts.mode || 'view';
        confirmVerb = opts.confirmVerb || 'Confirm';
        onConfirm = opts.onConfirm || null;
        onExtra = opts.onExtra || null;
        onClose = opts.onClose || null;
        selectedOption = null;
        selectedName = '';

        ui.stackTitle.textContent = opts.title || '';
        const cards = opts.cards || [];
        ui.stackList.innerHTML = cards.map((entry, i) => cardTile(entry, i)).join('')
            || '<div class="tiny stackpop-empty">No cards here.</div>';
        renderExtras(opts.extras);

        ui.stackConfirm.classList.toggle('hidden', mode !== 'select');
        ui.stackExtras.classList.toggle('hidden', !(opts.extras && opts.extras.length));

        const tiles = ui.stackList.querySelectorAll('.stackpop-card');
        // A single card is shown already expanded (and pre-selected when picking).
        if (tiles.length === 1) {
            expandTile(tiles[0], mode === 'select');
        } else {
            updateConfirm();
        }

        ui.stackModal.classList.add('open');
        ui.stackModal.setAttribute('aria-hidden', 'false');
    }

    function close() {
        if (!ui.stackModal.classList.contains('open')) return;
        ui.stackModal.classList.remove('open');
        ui.stackModal.setAttribute('aria-hidden', 'true');
        const cb = onClose;
        onConfirm = null;
        onExtra = null;
        onClose = null;
        if (cb) cb();
    }

    function isOpen() {
        return ui.stackModal.classList.contains('open');
    }

    // Wire events once.
    ui.stackList.addEventListener('click', (event) => {
        const tile = event.target.closest('.stackpop-card');
        if (!tile) return;
        expandTile(tile, mode === 'select');
    });

    ui.stackExtras.addEventListener('click', (event) => {
        const btn = event.target.closest('[data-value]');
        if (!btn) return;
        const value = btn.dataset.value;
        const cb = onExtra;
        close();
        if (cb) cb(value);
    });

    ui.stackConfirm.addEventListener('click', () => {
        if (selectedOption === null) return;
        const cb = onConfirm;
        const chosen = selectedOption;
        close();
        if (cb) cb(chosen);
    });

    ui.stackClose.addEventListener('click', close);
    ui.stackModal.addEventListener('click', (event) => {
        if (event.target === ui.stackModal) close();
    });

    return { open, close, isOpen };
}
