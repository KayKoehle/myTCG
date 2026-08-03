// The Replays screens: the saved-replay library, and the player that steps
// through a recording.
//
// Everything drawn here reads from the replay file alone — the boards it
// recorded and the card printings it bundled (js/replay.js). The live catalog
// and the rules engine are deliberately not consulted, which is what lets a
// replay from an older build show that build's cards and that build's numbers.

import { cardArtTag, escapeHtml, showToast, statChangeClass } from './helpers.js';
import {
    ReplayError,
    deckLabel,
    deleteReplay,
    exportReplay,
    expandFrames,
    formatAction,
    formatLogEntry,
    getReplay,
    laneName,
    listReplays,
    parseReplayText,
    readFileText,
    replayCard,
    saveReplay,
    seatOfCard,
    summarizeReplay,
} from './replay.js';

// Auto-play pacing. Each press of the speed button moves one step down.
const SPEEDS = [1, 2, 4, 0.5];
const BASE_STEP_MS = 900;

export function createReplayScreens(ui, { cardStack, onOpenPlayer }) {
    // The replay currently loaded into the player, its expanded steps, and
    // where the playhead is.
    let replay = null;
    let steps = [];
    let position = 0;
    let timer = null;
    let speedIdx = 0;

    // --- Seat naming ------------------------------------------------------

    function seatIdxOf(playerId) {
        const ids = (replay && replay.player_ids) || [];
        const idx = ids.indexOf(Number(playerId));
        return idx >= 0 ? idx : 0;
    }

    function seatLabel(seatIdx) {
        const meta = (replay && replay.client_meta) || {};
        const names = Array.isArray(meta.seat_names) ? meta.seat_names : [];
        const custom = (names[seatIdx] || '').trim();
        if (custom) return custom;
        const deck = ((replay && replay.deck_names) || [])[seatIdx];
        return deck ? deckLabel(deck) : `Player ${seatIdx + 1}`;
    }

    function seatShort(seatIdx) {
        return `P${seatIdx + 1}`;
    }

    // The log records player ids; formatLogEntry hands them straight back.
    const namePlayer = (playerId) => seatShort(seatIdxOf(playerId));

    // --- Library ----------------------------------------------------------

    function renderLibrary() {
        const entries = listReplays();
        if (!entries.length) {
            ui.replayList.innerHTML = `
                <div class="rp-empty">
                    <div class="rp-empty-icon" aria-hidden="true">🎬</div>
                    <p>No replays yet.</p>
                    <p class="tiny">Every match records itself. Finish a game — or tap
                    <strong>Save replay</strong> in its Past Actions sheet while it is still
                    running — and it turns up here, ready to export with a bug report.</p>
                </div>
            `;
            return;
        }
        ui.replayList.innerHTML = entries.map((entry) => `
            <div class="rp-item" data-replay-id="${escapeHtml(entry.id)}">
                <button class="rp-item-open" data-replay-open="${escapeHtml(entry.id)}">
                    <span class="rp-item-title">${escapeHtml(entry.title || 'Match')}</span>
                    <span class="rp-item-sub">
                        <span class="rp-badge rp-badge-${escapeHtml(String(entry.outcome || '').toLowerCase())}">${escapeHtml(entry.outcome || '')}</span>
                        <span>${escapeHtml(entry.mode || '')}</span>
                        <span>${entry.steps || 0} steps</span>
                    </span>
                    <span class="rp-item-meta tiny">${escapeHtml(entry.recordedAtLabel || '')} · build ${escapeHtml(entry.appVersion || '?')}${entry.source === 'import' ? ' · imported' : ''}</span>
                </button>
                <div class="rp-item-actions">
                    <button class="btn ghost tiny" data-replay-export="${escapeHtml(entry.id)}">Export</button>
                    <button class="btn ghost tiny rp-danger" data-replay-delete="${escapeHtml(entry.id)}">Delete</button>
                </div>
            </div>
        `).join('');
    }

    async function onLibraryClick(event) {
        const open = event.target.closest('[data-replay-open]');
        if (open) {
            onOpenPlayer(open.getAttribute('data-replay-open'));
            return;
        }
        const exportBtn = event.target.closest('[data-replay-export]');
        if (exportBtn) {
            const saved = getReplay(exportBtn.getAttribute('data-replay-export'));
            if (!saved) { showToast('That replay could not be read.'); return; }
            await exportAndReport(saved);
            return;
        }
        const deleteBtn = event.target.closest('[data-replay-delete]');
        if (deleteBtn) {
            // Two taps to delete: the button becomes its own confirmation, the
            // same way the deck editor guards a deck deletion.
            if (deleteBtn.dataset.confirming === 'yes') {
                deleteReplay(deleteBtn.getAttribute('data-replay-delete'));
                renderLibrary();
            } else {
                deleteBtn.dataset.confirming = 'yes';
                deleteBtn.textContent = 'Really delete?';
            }
        }
    }

    async function exportAndReport(target) {
        try {
            const how = await exportReplay(target);
            if (how === 'shared' || how === 'downloaded') showToast('Replay exported.');
            else if (how === 'copied-code') showToast('Replay code copied — paste it into your bug report.');
            else if (how === 'copied-json') showToast('Replay copied to the clipboard.');
            else if (how === 'unavailable') showToast('This device offers no way to export the file.');
        } catch (error) {
            showToast(error.message || 'Export failed.');
        }
    }

    async function importFromText(text) {
        try {
            const imported = await parseReplayText(text);
            const entry = saveReplay(imported, { source: 'import' });
            renderLibrary();
            showToast('Replay imported.');
            return entry;
        } catch (error) {
            showToast(error instanceof ReplayError ? error.message : 'That replay could not be imported.');
            return null;
        }
    }

    // --- Player -----------------------------------------------------------

    function load(id) {
        const loaded = getReplay(id);
        if (!loaded) {
            showToast('That replay could not be read.');
            return false;
        }
        try {
            steps = expandFrames(loaded);
        } catch (error) {
            showToast(error.message || 'That replay could not be read.');
            return false;
        }
        replay = loaded;
        position = 0;
        stop();
        renderHeader();
        render();
        return true;
    }

    function renderHeader() {
        const summary = summarizeReplay(replay);
        ui.replayTitle.textContent = summary.title;
        ui.replayMeta.innerHTML = `
            <span>${escapeHtml(summary.mode)}</span>
            <span>${escapeHtml(summary.recordedAtLabel)}</span>
            <span class="rp-badge rp-badge-${escapeHtml(summary.outcome.toLowerCase())}">${escapeHtml(summary.outcome)}</span>
            <span title="The build that recorded this match">build ${escapeHtml(summary.appVersion)}</span>
            <span title="Identifies the card printings in this replay — the same matchup recorded after a rebalance reads differently here">cards ${escapeHtml(summary.cardFingerprint || 'unknown')}</span>
            ${summary.truncated ? '<span class="rp-warn">recording was cut short</span>' : ''}
        `;
        ui.replayRange.max = String(Math.max(0, steps.length - 1));
    }

    function current() {
        return steps[Math.min(position, steps.length - 1)] || { state: {}, log: [], newLog: [] };
    }

    function seatCount() {
        return ((replay && replay.player_ids) || []).length;
    }

    // A card as it was printed when the match was played, with the live power
    // the engine gave it at this step layered on top.
    function cardTile(cardId, { power = null, facedown = false, highlight = false, extraClass = '', badge = '' } = {}) {
        const card = replayCard(replay, cardId);
        const seat = seatOfCard(replay, cardId);
        if (facedown) {
            return `<span class="rp-card rp-card-facedown" title="Face-down card">
                <span class="rp-card-name">Face-down</span>
            </span>`;
        }
        if (!card) {
            // A card the recording never printed. Show the id rather than
            // silently dropping it — in a bug report that gap is the finding.
            return `<span class="rp-card rp-card-unknown" title="${escapeHtml(String(cardId))}">
                <span class="rp-card-name">Unknown card</span>
            </span>`;
        }
        const shown = power === null || power === undefined ? card.power : power;
        const powerCls = statChangeClass(shown, card.power, true);
        return `
            <button type="button" class="rp-card seat-${seat} ${highlight ? 'rp-card-hot' : ''} ${extraClass}"
                data-card-id="${escapeHtml(cardId)}" title="${escapeHtml(card.name)}">
                <span class="rp-card-cost">${escapeHtml(String(card.cost ?? '?'))}</span>
                <span class="rp-card-art">${cardArtTag(card.name, 'rp-card-img', { eager: true })}</span>
                <span class="rp-card-name">${escapeHtml(card.name)}</span>
                <span class="rp-card-power ${powerCls}">${escapeHtml(String(shown ?? '?'))}</span>
                ${badge}
            </button>
        `;
    }

    function renderLanes(state, hotCardId) {
        const locations = state.locations || [];
        const seats = seatCount();
        const facedown = new Set(state.facedown || []);
        return locations.map((loc) => {
            const rows = [];
            for (let seat = 0; seat < seats; seat += 1) {
                const stack = (loc.stacks || [])[seat] || [];
                const cards = stack.map((cardId) => cardTile(cardId, {
                    power: (loc.powers || {})[cardId],
                    facedown: facedown.has(cardId),
                    highlight: cardId === hotCardId,
                })).join('');
                rows.push(`
                    <div class="rp-lane-row seat-${seat}">
                        <span class="rp-lane-seat">${escapeHtml(seatShort(seat))}</span>
                        <div class="rp-lane-cards">${cards || '<span class="rp-lane-empty">—</span>'}</div>
                        <span class="rp-lane-power">${escapeHtml(String((loc.side_power || [])[seat] ?? 0))}</span>
                    </div>
                `);
            }
            const label = laneName(Number(loc.location_id), locations.length);
            return `
                <section class="rp-lane">
                    <header class="rp-lane-head">
                        <span>${escapeHtml(label.charAt(0).toUpperCase() + label.slice(1))}</span>
                        <span class="tiny">capacity ${escapeHtml(String(loc.capacity ?? '?'))}${Number(loc.weight) > 1 ? ` · weight ${escapeHtml(String(loc.weight))}` : ''}</span>
                    </header>
                    ${rows.join('')}
                </section>
            `;
        }).join('');
    }

    function renderSeats(state, hotCardId) {
        const seats = seatCount();
        const facedown = new Set(state.facedown || []);
        const acting = state.acting_player_id;
        const out = [];
        for (let seat = 0; seat < seats; seat += 1) {
            const playerId = (replay.player_ids || [])[seat];
            const hand = (state.hands || [])[seat] || [];
            const costs = (state.hand_costs || [])[seat] || [];
            const underworld = (state.underworlds || [])[seat] || [];
            const setAside = (state.set_aside || [])[seat] || [];
            const deckSize = ((state.decks || [])[seat] || []).length;
            const isActing = Number(acting) === Number(playerId);
            out.push(`
                <section class="rp-seat seat-${seat} ${isActing ? 'rp-seat-acting' : ''}">
                    <header class="rp-seat-head">
                        <span class="rp-seat-name">${escapeHtml(seatShort(seat))} · ${escapeHtml(seatLabel(seat))}</span>
                        <span class="rp-seat-stats">
                            <span title="Crowns">👑 ${escapeHtml(String((state.victory_points || [])[seat] ?? 0))}</span>
                            <span title="Mana">💧 ${escapeHtml(String((state.mana_pool || [])[seat] ?? 0))}/${escapeHtml(String((state.mana_cap || [])[seat] ?? 0))}</span>
                            <span title="Cards left in deck">🂠 ${deckSize}</span>
                        </span>
                    </header>
                    <div class="rp-zone">
                        <span class="rp-zone-label">Hand ${hand.length}</span>
                        <div class="rp-zone-cards">${hand.map((cardId, i) => cardTile(cardId, {
                            highlight: cardId === hotCardId,
                            extraClass: 'rp-card-hand',
                            // The cost after the discounts that were live at
                            // this exact moment, not the printed one.
                            badge: costs[i] === undefined ? '' : `<span class="rp-card-live-cost" title="What this cost to play at this moment">${escapeHtml(String(costs[i]))}</span>`,
                        })).join('') || '<span class="rp-lane-empty">—</span>'}</div>
                    </div>
                    <div class="rp-zone">
                        <span class="rp-zone-label">Underworld ${underworld.length}</span>
                        <div class="rp-zone-cards">${underworld.map((cardId) => cardTile(cardId, { highlight: cardId === hotCardId })).join('') || '<span class="rp-lane-empty">—</span>'}</div>
                    </div>
                    ${setAside.length ? `<div class="rp-zone">
                        <span class="rp-zone-label">Set aside ${setAside.length}</span>
                        <div class="rp-zone-cards">${setAside.map((cardId) => cardTile(cardId, { facedown: facedown.has(cardId) })).join('')}</div>
                    </div>` : ''}
                    <div class="rp-zone rp-zone-deck">
                        <span class="rp-zone-label">Deck ${deckSize}</span>
                        <div class="rp-zone-cards rp-deck-order">${((state.decks || [])[seat] || []).map((cardId) => cardTile(cardId)).join('') || '<span class="rp-lane-empty">—</span>'}</div>
                    </div>
                </section>
            `);
        }
        return out.join('');
    }

    function render() {
        const step = current();
        const state = step.state || {};
        const hotCardId = (step.action && step.action.card_id) || null;

        ui.replayStatus.innerHTML = `
            <span>Round ${escapeHtml(String(state.round_number ?? '?'))}</span>
            <span>Turn ${escapeHtml(String(state.turn_number ?? '?'))}</span>
            <span>${escapeHtml(String(state.phase || ''))}</span>
            ${state.acting_player_id !== null && state.acting_player_id !== undefined
                ? `<span>waiting on ${escapeHtml(namePlayer(state.acting_player_id))}</span>` : ''}
            ${state.flood_used ? '<span class="rp-warn">the flood has come</span>'
                : (state.flood_pending_turn ? '<span class="rp-warn">flood pending</span>' : '')}
        `;

        const pending = state.pending_choice;
        ui.replayPending.innerHTML = pending
            ? `<div class="rp-pending"><strong>${escapeHtml(namePlayer(pending.player_id))} must choose:</strong>
                 ${escapeHtml(pending.prompt || pending.choice_kind || '')}</div>`
            : '';

        ui.replayLanes.innerHTML = renderLanes(state, hotCardId);
        ui.replaySeats.innerHTML = renderSeats(state, hotCardId);

        const actionText = formatAction(replay, step.action, namePlayer);
        ui.replayStepLabel.textContent = `Step ${position + 1} / ${steps.length}${actionText ? ` — ${actionText}` : ''}`;
        ui.replayRange.value = String(position);
        ui.btnReplayPlay.textContent = timer ? '⏸' : '▶';
        ui.btnReplayPlay.setAttribute('aria-label', timer ? 'Pause' : 'Play');
        ui.btnReplaySpeed.textContent = `${SPEEDS[speedIdx]}×`;

        renderLog(step);
    }

    function renderLog(step) {
        const freshFrom = step.log.length - step.newLog.length;
        ui.replayLog.innerHTML = step.log.map((entry, i) => `
            <button type="button" class="rp-log-line ${i >= freshFrom ? 'rp-log-fresh' : ''}"
                data-log-index="${i}">${escapeHtml(formatLogEntry(replay, entry, namePlayer))}</button>
        `).join('') || '<div class="tiny rp-lane-empty">Nothing has happened yet.</div>';
        const fresh = ui.replayLog.querySelector('.rp-log-fresh');
        if (fresh) fresh.scrollIntoView({ block: 'nearest' });
        else ui.replayLog.scrollTop = ui.replayLog.scrollHeight;
    }

    // --- Transport --------------------------------------------------------

    function seek(next) {
        position = Math.max(0, Math.min(steps.length - 1, next));
        render();
    }

    function play() {
        if (timer || !steps.length) return;
        if (position >= steps.length - 1) position = 0;
        timer = setInterval(() => {
            if (position >= steps.length - 1) { stop(); render(); return; }
            position += 1;
            render();
        }, BASE_STEP_MS / SPEEDS[speedIdx]);
        render();
    }

    function stop() {
        if (timer) clearInterval(timer);
        timer = null;
    }

    function togglePlay() {
        if (timer) { stop(); render(); } else { play(); }
    }

    // Leaving the screen must not leave a timer redrawing a hidden board.
    function close() {
        stop();
    }

    // The first log line a step produced jumps the playhead to that step.
    function jumpToLogIndex(logIndex) {
        for (let i = 0; i < steps.length; i += 1) {
            if (steps[i].log.length > logIndex) { seek(i); return; }
        }
    }

    function openCard(cardId) {
        const card = replayCard(replay, cardId);
        if (!card) return;
        // The printing from the replay, not the live catalog — an old replay
        // opens the card as it read back then.
        cardStack.open({ mode: 'view', title: card.name, cards: [{ card }] });
    }

    // --- Wiring -----------------------------------------------------------

    function init() {
        ui.replayList.addEventListener('click', onLibraryClick);

        ui.btnReplayImport.addEventListener('click', () => ui.replayFileInput.click());
        ui.replayFileInput.addEventListener('change', async () => {
            const file = ui.replayFileInput.files && ui.replayFileInput.files[0];
            ui.replayFileInput.value = '';
            if (!file) return;
            try {
                await importFromText(await readFileText(file));
            } catch (error) {
                showToast(error.message || 'That file could not be read.');
            }
        });
        ui.btnReplayPaste.addEventListener('click', async () => {
            let text = '';
            if (navigator.clipboard && navigator.clipboard.readText) {
                try { text = await navigator.clipboard.readText(); } catch (error) { /* ask instead */ }
            }
            if (!text) text = window.prompt('Paste the replay code or the contents of a .mytcgreplay file:') || '';
            if (text.trim()) await importFromText(text);
        });

        ui.btnReplayExportCurrent.addEventListener('click', () => {
            if (replay) exportAndReport(replay);
        });
        ui.btnReplayFirst.addEventListener('click', () => { stop(); seek(0); });
        ui.btnReplayPrev.addEventListener('click', () => { stop(); seek(position - 1); });
        ui.btnReplayNext.addEventListener('click', () => { stop(); seek(position + 1); });
        ui.btnReplayLast.addEventListener('click', () => { stop(); seek(steps.length - 1); });
        ui.btnReplayPlay.addEventListener('click', togglePlay);
        ui.btnReplaySpeed.addEventListener('click', () => {
            speedIdx = (speedIdx + 1) % SPEEDS.length;
            if (timer) { stop(); play(); } else { render(); }
        });
        ui.replayRange.addEventListener('input', () => { stop(); seek(Number(ui.replayRange.value)); });

        ui.replayLog.addEventListener('click', (event) => {
            const line = event.target.closest('[data-log-index]');
            if (line) { stop(); jumpToLogIndex(Number(line.getAttribute('data-log-index'))); }
        });
        for (const zone of [ui.replayLanes, ui.replaySeats]) {
            zone.addEventListener('click', (event) => {
                const tile = event.target.closest('[data-card-id]');
                if (tile) openCard(tile.getAttribute('data-card-id'));
            });
        }
    }

    return { init, renderLibrary, load, close };
}
