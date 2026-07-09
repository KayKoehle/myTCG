import { cardArtTag, effectLabel, escapeHtml, typeLabel } from './helpers.js';

// A reusable card-stack popup. Three modes:
//   'select'      - pick one card from a stack, then press a confirm button
//                    (e.g. "Banish Achilles", "Move Enkidu").
//   'multi-select' - pick several individual cards (not a combination from a
//                    prebuilt list) until the selection matches one of the
//                    engine's valid combos, then confirm (e.g. "Banish two
//                    of your beings", the Trojan Horse payload).
//   'view'        - browse a stack of cards read-only (e.g. an underworld pile).
// Cards render collapsed (headline + type) and expand to full art + effect on
// tap in 'select'/'view' mode; 'multi-select' always shows full detail since
// picking requires reading every candidate's effect.
export function createCardStackPopup(ui) {
    let mode = 'view';
    let selectedOption = null;
    let selectedName = '';
    let confirmVerb = 'Confirm';
    let onConfirm = null;
    let onExtra = null;
    let onClose = null;
    // multi-select state
    let comboOptions = [];
    let selectedIds = new Set();
    let cardNameByCardId = new Map();

    function cardTile({ card, option }, index) {
        const cost = card.cost ?? '?';
        const power = (card.power !== null && card.power !== undefined) ? card.power : '?';
        const type = typeLabel(card);
        const id = card.id ?? option ?? String(index);
        return `
            <div class="stackpop-card" data-option="${escapeHtml(String(option ?? index))}" data-id="${escapeHtml(String(id))}" data-name="${escapeHtml(card.name || '')}">
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

    // In multi-select mode, a combo is valid the moment the selected card ids
    // (in any order) match one of the engine's pipe-joined combo options.
    function matchingCombo() {
        const chosen = Array.from(selectedIds).sort();
        if (chosen.length === 0) return null;
        return comboOptions.find((opt) => {
            const parts = String(opt).split('|').sort();
            return parts.length === chosen.length && parts.every((part, i) => part === chosen[i]);
        }) || null;
    }

    function updateMultiConfirm() {
        const match = matchingCombo();
        selectedOption = match;
        ui.stackConfirm.disabled = !match;
        if (match) {
            const names = Array.from(selectedIds).map((id) => cardNameByCardId.get(id) || id);
            ui.stackConfirm.textContent = `${confirmVerb} ${names.join(', ')}`.trim();
        } else if (selectedIds.size === 0) {
            ui.stackConfirm.textContent = `Select cards to ${confirmVerb.toLowerCase()}`;
        } else {
            ui.stackConfirm.textContent = 'Selection is not valid — try a different combination';
        }
    }

    function toggleMultiTile(tile) {
        const id = tile.dataset.id;
        if (!id) return;
        if (selectedIds.has(id)) {
            selectedIds.delete(id);
            tile.classList.remove('selected');
        } else {
            selectedIds.add(id);
            tile.classList.add('selected');
        }
        updateMultiConfirm();
    }

    function renderExtras(extras, note) {
        const noteHtml = note ? `<div class="stackpop-note">${escapeHtml(note)}</div>` : '';
        ui.stackExtras.innerHTML = noteHtml + (extras || [])
            .map((opt) => `<button class="choice-option-btn stackpop-extra${opt.kind ? ` stackpop-extra-${opt.kind}` : ''}" data-value="${escapeHtml(String(opt.value))}">${escapeHtml(opt.label)}</button>`)
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
        selectedIds = new Set();
        comboOptions = opts.comboOptions || [];
        cardNameByCardId = new Map();

        ui.stackTitle.textContent = opts.title || '';
        const cards = opts.cards || [];
        for (const entry of cards) {
            if (entry && entry.card && entry.card.id) cardNameByCardId.set(entry.card.id, entry.card.name);
        }
        ui.stackList.innerHTML = cards.map((entry, i) => cardTile(entry, i)).join('')
            || '<div class="tiny stackpop-empty">No cards here.</div>';
        renderExtras(opts.extras, opts.note);

        ui.stackConfirm.classList.toggle('hidden', mode !== 'select' && mode !== 'multi-select');
        ui.stackExtras.classList.toggle('hidden', !(opts.extras && opts.extras.length) && !opts.note);

        const tiles = ui.stackList.querySelectorAll('.stackpop-card');
        if (opts.expandAll) {
            // The collection popup shows every card fully readable up front.
            tiles.forEach((tile) => tile.classList.add('expanded'));
        }
        if (mode === 'multi-select') {
            // Nothing to expand/collapse: picking requires seeing every
            // candidate's effect up front.
            tiles.forEach((tile) => tile.classList.add('expanded'));
            updateMultiConfirm();
        } else if (tiles.length === 1) {
            // A single card is shown already expanded (and pre-selected when picking).
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
        if (mode === 'multi-select') {
            toggleMultiTile(tile);
        } else {
            expandTile(tile, mode === 'select');
        }
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
