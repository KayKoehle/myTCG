// Testing mode: a sandbox over the rules engine for playtesters.
//
// Unlike the game screen this view is omniscient and fully editable — every
// zone of every seat is visible, any card can be dragged (well: tapped) into
// any zone, every seat can be driven by hand or handed to an AI agent, and
// every step lands on an undo stack. The whole state lives on the server
// (engine/sandbox.py); this module renders the view it returns and posts edits
// back, so the rules engine is always the single source of truth.
//
// Rendering is deliberately dumb: every response re-renders the screen from
// scratch and all interaction runs through one delegated click/change handler
// keyed on data-sb attributes. A playtest tool changes shape often, and this
// keeps adding a control to a one-line affair.

import { postJson } from './api.js';
import { cardPngUrl, escapeHtml, showToast } from './helpers.js';

const AGENTS = [
    { id: 'search', label: 'Search (greedy)' },
    { id: 'minimax', label: 'Minimax (depth 3)' },
    { id: 'neural', label: 'Neural policy' },
    { id: 'random', label: 'Random' },
    { id: 'ladder', label: 'Rated ladder' },
];

const ZONE_LABELS = {
    hand: 'Hand',
    deck: 'Deck',
    underworld: 'Underworld',
    set_aside: 'Set aside',
    location: 'In play',
    removed: 'Out of the game',
};

const PHASES = ['MULLIGAN', 'DRAW', 'MAIN', 'GAME_OVER'];

export function createSandboxController(ui) {
    let view = null; // the last sandbox view from the server
    let catalog = []; // every printed card, for the "add a card" picker
    let deckOptions = []; // deck names selectable in the setup row
    let matchId = `sandbox-${Math.floor(Math.random() * 1_000_000)}`;
    let onExit = null;

    // New-match setup (the row shown until a match exists, and on demand).
    let setup = { players: 2, decks: [], seed: 42, skipMulligan: true, open: true };
    // AI panel state.
    let ai = { playerId: null, agent: 'search', elo: 1200 };
    let analysis = null;
    // Which seat's legal actions the "drive a seat" panel lists; null follows
    // whoever the engine is waiting on.
    let controlPlayerId = null;
    let busy = false;

    // Open popovers.
    let cardMenu = null; // { cardId, seatPlayerId }
    let picker = null; // { zone, playerId, locationId, query }

    // --- server calls ------------------------------------------------------

    async function call(path, body, { keepAnalysis = false } = {}) {
        if (busy) return null;
        busy = true;
        setStatus('working…');
        try {
            const data = await postJson(path, { match_id: matchId, ...body });
            if (data.sandbox) {
                view = data.sandbox;
                if (!keepAnalysis) analysis = null;
                if (ai.playerId === null) ai.playerId = view.acting_player_id || view.players[0];
            }
            setStatus('');
            render();
            return data;
        } catch (error) {
            setStatus('');
            showToast(String(error && error.message ? error.message : error));
            render();
            return null;
        } finally {
            busy = false;
        }
    }

    function setStatus(text) {
        if (ui.sandboxStatus) ui.sandboxStatus.textContent = text;
    }

    async function ensureCatalog() {
        if (catalog.length) return;
        try {
            const data = await postJson('/api/sandbox/catalog', {});
            catalog = data.cards || [];
            deckOptions = data.decks || [];
            if (!setup.decks.length) {
                setup.decks = [
                    deckOptions.includes('epic_of_gilgamesh') ? 'epic_of_gilgamesh' : (deckOptions[0] || ''),
                    deckOptions.includes('siege_of_troy') ? 'siege_of_troy' : (deckOptions[1] || deckOptions[0] || ''),
                ];
            }
        } catch (error) {
            showToast(`Could not load the card catalog: ${error}`);
        }
    }

    async function createMatch() {
        const decks = setup.decks.slice(0, setup.players).map((d) => d || deckOptions[0]);
        matchId = `sandbox-${Math.floor(Math.random() * 1_000_000)}`;
        analysis = null;
        controlPlayerId = null;
        ai.playerId = null;
        setup.open = false;
        await call('/api/sandbox/create', {
            decks,
            seed: Number(setup.seed) || 42,
            skip_mulligan: Boolean(setup.skipMulligan),
        });
    }

    // --- entry point -------------------------------------------------------

    async function open(options = {}) {
        onExit = options.onExit || onExit;
        await ensureCatalog();
        if (!view) {
            await createMatch();
        } else {
            render();
        }
    }

    // --- small render helpers ---------------------------------------------

    function seatName(playerId) {
        const seat = (view.seats || []).find((s) => Number(s.player_id) === Number(playerId));
        return seat ? `P${seat.player_id}` : `P${playerId}`;
    }

    function seatOf(playerId) {
        return (view.seats || []).find((s) => Number(s.player_id) === Number(playerId)) || null;
    }

    function cardChip(card, context) {
        // context: { zone, playerId, locationId } — carried on the chip so the
        // card menu knows where the card is coming from.
        const power = card.power ?? '';
        const cost = card.playable_cost ?? card.cost ?? '';
        const flags = [];
        if (card.facedown) flags.push('<span class="sb-flag">face-down</span>');
        if (card.owner_player_id && context.playerId && Number(card.owner_player_id) !== Number(context.playerId)) {
            flags.push(`<span class="sb-flag sb-flag-owner">owner P${card.owner_player_id}</span>`);
        }
        return `
            <button type="button" class="sb-chip" data-sb="card" data-card-id="${escapeHtml(card.id)}"
                data-zone="${escapeHtml(context.zone)}" data-player-id="${context.playerId ?? ''}"
                data-location-id="${context.locationId ?? ''}" title="${escapeHtml(card.effect || '')}">
                <span class="sb-chip-cost">${cost}</span>
                <span class="sb-chip-name">${escapeHtml(card.name)}</span>
                <span class="sb-chip-power">${power}</span>
                ${flags.join('')}
            </button>`;
    }

    function boardCard(card, context) {
        return `
            <button type="button" class="sb-boardcard ${card.facedown ? 'facedown' : ''}" data-sb="card"
                data-card-id="${escapeHtml(card.id)}" data-zone="location" data-player-id="${context.playerId}"
                data-location-id="${context.locationId}" title="${escapeHtml(card.effect || '')}">
                <img src="${cardPngUrl(card.name)}" alt="${escapeHtml(card.name)}" draggable="false" loading="lazy"
                    onerror="this.style.display='none';">
                <span class="sb-boardcard-name">${escapeHtml(card.name)}</span>
                <span class="sb-boardcard-power">${card.power ?? card.base_power ?? ''}</span>
            </button>`;
    }

    function zoneBlock(seat, zone, cards) {
        const canClear = cards.length > 0;
        return `
            <div class="sb-zone" data-zone="${zone}">
                <div class="sb-zone-head">
                    <span class="sb-zone-title">${ZONE_LABELS[zone]} <span class="sb-count">${cards.length}</span></span>
                    <span class="sb-zone-actions">
                        <button type="button" class="sb-mini" data-sb="add" data-zone="${zone}"
                            data-player-id="${seat.player_id}">+ Card</button>
                        <button type="button" class="sb-mini ghost" data-sb="clear-zone" data-zone="${zone}"
                            data-player-id="${seat.player_id}" ${canClear ? '' : 'disabled'}>Clear</button>
                        ${zone === 'deck' ? `<button type="button" class="sb-mini ghost" data-sb="shuffle"
                            data-player-id="${seat.player_id}">Shuffle</button>` : ''}
                    </span>
                </div>
                <div class="sb-zone-cards">
                    ${cards.length
                        ? cards.map((card) => cardChip(card, { zone, playerId: seat.player_id })).join('')
                        : '<span class="sb-empty">empty</span>'}
                </div>
            </div>`;
    }

    // --- toolbar -----------------------------------------------------------

    function renderToolbar() {
        const setupRow = renderSetupRow();
        if (!view) {
            ui.sandboxToolbar.innerHTML = setupRow;
            return;
        }
        const acting = view.acting_player_id;
        ui.sandboxToolbar.innerHTML = `
            <div class="sb-toolbar-row">
                <div class="sb-group">
                    <button type="button" class="sb-btn" data-sb="undo" ${view.can_undo ? '' : 'disabled'}>↶ Undo</button>
                    <button type="button" class="sb-btn" data-sb="redo" ${view.can_redo ? '' : 'disabled'}>↷ Redo</button>
                    <button type="button" class="sb-btn ghost" data-sb="reset">⟲ Reset</button>
                </div>
                <div class="sb-group sb-state">
                    <label class="sb-field">Phase
                        <select data-sb-input="phase">
                            ${PHASES.map((p) => `<option value="${p}" ${p === view.phase ? 'selected' : ''}>${p}</option>`).join('')}
                        </select>
                    </label>
                    <label class="sb-field">Turn of
                        <select data-sb-input="current">
                            ${view.players.map((pid) => `<option value="${pid}" ${Number(pid) === Number(view.current_player_id) ? 'selected' : ''}>P${pid}</option>`).join('')}
                        </select>
                    </label>
                    <label class="sb-field">Turn
                        <input type="number" min="1" value="${view.turn_number}" data-sb-input="counter" data-counter="turn_number">
                    </label>
                    <label class="sb-field">Round
                        <input type="number" min="1" value="${view.round_number}" data-sb-input="counter" data-counter="round_number">
                    </label>
                    <span class="sb-pill ${view.terminal ? 'danger' : ''}">
                        ${view.terminal ? 'Game over' : `to act: ${acting ? seatName(acting) : '—'}`}
                    </span>
                </div>
                <div class="sb-group">
                    <button type="button" class="sb-btn ghost" data-sb="toggle-setup">New match</button>
                    <button type="button" class="sb-btn ghost" data-sb="export">Export</button>
                    <button type="button" class="sb-btn ghost" data-sb="import">Import</button>
                </div>
            </div>
            ${setup.open ? setupRow : ''}`;
    }

    function renderSetupRow() {
        const seats = [];
        for (let i = 0; i < setup.players; i += 1) {
            const value = setup.decks[i] || deckOptions[0] || '';
            seats.push(`
                <label class="sb-field">P${i + 1} deck
                    <select data-sb-input="setup-deck" data-seat="${i}">
                        ${deckOptions.map((deck) => `<option value="${deck}" ${deck === value ? 'selected' : ''}>${escapeHtml(deck)}</option>`).join('')}
                    </select>
                </label>`);
        }
        return `
            <div class="sb-setup">
                <label class="sb-field">Players
                    <select data-sb-input="setup-players">
                        ${[2, 3, 4, 5, 6].map((n) => `<option value="${n}" ${n === setup.players ? 'selected' : ''}>${n}</option>`).join('')}
                    </select>
                </label>
                ${seats.join('')}
                <label class="sb-field">Seed
                    <input type="number" value="${setup.seed}" data-sb-input="setup-seed">
                </label>
                <label class="sb-check">
                    <input type="checkbox" data-sb-input="setup-mulligan" ${setup.skipMulligan ? 'checked' : ''}>
                    Skip mulligan
                </label>
                <button type="button" class="sb-btn primary" data-sb="create">Deal new match</button>
            </div>`;
    }

    // --- board -------------------------------------------------------------

    function renderBoard() {
        if (!view) {
            ui.sandboxBoard.innerHTML = '';
            return;
        }
        ui.sandboxBoard.innerHTML = (view.locations || []).map((loc) => {
            const rows = view.players.map((pid) => {
                const cards = (loc.stacks || {})[String(pid)] || [];
                const reachable = (loc.accessible || []).map(Number).includes(Number(pid));
                return `
                    <div class="sb-lane-row ${reachable ? '' : 'unreachable'}">
                        <div class="sb-lane-side">
                            <span class="sb-lane-seat">${seatName(pid)}</span>
                            <span class="sb-lane-power">${(loc.side_power || {})[String(pid)] ?? 0}</span>
                            <button type="button" class="sb-mini" data-sb="add" data-zone="location"
                                data-player-id="${pid}" data-location-id="${loc.location_id}">+</button>
                        </div>
                        <div class="sb-lane-cards">
                            ${cards.length
                                ? cards.map((card) => boardCard(card, { playerId: pid, locationId: loc.location_id })).join('')
                                : '<span class="sb-empty">empty</span>'}
                        </div>
                    </div>`;
            }).join('');
            return `
                <section class="sb-lane">
                    <header class="sb-lane-head">
                        <span class="sb-lane-name">${escapeHtml(loc.label)}</span>
                        <span class="sb-lane-meta">${loc.total_cards}/${loc.capacity} · weight ${loc.weight}</span>
                    </header>
                    ${rows}
                </section>`;
        }).join('');
    }

    // --- seats -------------------------------------------------------------

    function renderSeats() {
        if (!view) {
            ui.sandboxSeats.innerHTML = '';
            return;
        }
        ui.sandboxSeats.innerHTML = (view.seats || []).map((seat) => {
            const isActing = Number(view.acting_player_id) === Number(seat.player_id);
            const discounts = Object.entries(seat.discounts || {})
                .filter(([, value]) => Number(value) !== 0)
                .map(([key, value]) => `<span class="sb-flag">${key.replace(/^next_/, '').replace(/_/g, ' ')}: ${value}</span>`)
                .join('');
            return `
                <section class="sb-seat ${isActing ? 'acting' : ''}">
                    <header class="sb-seat-head">
                        <span class="sb-seat-name">${seatName(seat.player_id)}</span>
                        <span class="sb-seat-deck">${escapeHtml(seat.deck_name)}</span>
                        <span class="sb-seat-eval" title="AI evaluation of the position from this seat">eval ${seat.evaluation}</span>
                    </header>
                    <div class="sb-stats">
                        <label class="sb-field">Mana
                            <input type="number" min="0" value="${seat.mana}" data-sb-input="stat" data-stat="mana_pool"
                                data-player-id="${seat.player_id}">
                        </label>
                        <label class="sb-field">Crowns
                            <input type="number" min="0" value="${seat.victory_points}" data-sb-input="stat"
                                data-stat="victory_points" data-player-id="${seat.player_id}">
                        </label>
                        <label class="sb-field">Turns
                            <input type="number" min="0" value="${seat.turn_count}" data-sb-input="stat"
                                data-stat="player_turn_counts" data-player-id="${seat.player_id}"
                                title="Turns taken — this is what caps mana (min(7, turns))">
                        </label>
                        <span class="sb-pill tiny">mana cap ${seat.mana_cap}</span>
                        ${seat.mulligan_done ? '' : '<span class="sb-flag">mulligan pending</span>'}
                        ${seat.protected_location !== null && seat.protected_location !== undefined
                            ? `<span class="sb-flag">protected lane ${seat.protected_location + 1}</span>` : ''}
                        ${discounts}
                    </div>
                    ${zoneBlock(seat, 'hand', seat.hand)}
                    ${zoneBlock(seat, 'deck', seat.deck)}
                    ${zoneBlock(seat, 'underworld', seat.underworld)}
                    ${zoneBlock(seat, 'set_aside', seat.set_aside)}
                </section>`;
        }).join('');
    }

    // --- side panels -------------------------------------------------------

    function actionButton(action) {
        return `
            <button type="button" class="sb-action" data-sb="action" data-kind="${escapeHtml(action.kind)}"
                data-player-id="${action.player_id}" data-card-id="${escapeHtml(action.card_id || '')}"
                data-location-id="${action.location_id ?? ''}" data-option-id="${escapeHtml(action.option_id || '')}">
                ${escapeHtml(action.label)}
            </button>`;
    }

    function renderDrivePanel() {
        const acting = view.acting_player_id;
        // Once the match is over nobody is "acting", so fall back to a real
        // seat — the panel still offers that seat's (empty) action list.
        const listedPlayer = controlPlayerId ?? acting ?? view.players[0];
        const actions = (view.legal_actions || []).filter((a) => Number(a.player_id) === Number(listedPlayer));
        const pending = view.pending_choice;
        return `
            <section class="sb-panel">
                <header class="sb-panel-head">
                    <h3>Drive a seat</h3>
                    <select data-sb-input="control-seat">
                        ${view.players.map((pid) => `<option value="${pid}" ${Number(pid) === Number(listedPlayer) ? 'selected' : ''}>${seatName(pid)}${Number(pid) === Number(acting) ? ' — to act' : ''}</option>`).join('')}
                    </select>
                </header>
                ${pending ? `
                    <div class="sb-pending">
                        <div class="sb-pending-head">${seatName(pending.player_id)} must choose —
                            <em>${escapeHtml(pending.source_card_name || '')}</em></div>
                        <div class="sb-pending-prompt">${escapeHtml(pending.prompt)}</div>
                        <button type="button" class="sb-mini ghost" data-sb="clear-choice">Cancel this choice</button>
                    </div>` : ''}
                <div class="sb-actions">
                    ${actions.length
                        ? actions.map(actionButton).join('')
                        : `<span class="sb-empty">${view.terminal ? 'The match is over.' : `${seatName(listedPlayer)} has no legal action right now.`}</span>`}
                </div>
                <div class="sb-panel-foot">
                    <button type="button" class="sb-mini ghost" data-sb="action" data-kind="surrender"
                        data-player-id="${listedPlayer}">Surrender ${seatName(listedPlayer)}</button>
                </div>
            </section>`;
    }

    function renderAiPanel() {
        const rows = (analysis && analysis.actions) || [];
        const best = rows.length ? rows[0] : null;
        return `
            <section class="sb-panel">
                <header class="sb-panel-head"><h3>AI</h3></header>
                <div class="sb-ai-controls">
                    <label class="sb-field">Seat
                        <select data-sb-input="ai-seat">
                            ${view.players.map((pid) => `<option value="${pid}" ${Number(pid) === Number(ai.playerId) ? 'selected' : ''}>${seatName(pid)}${Number(pid) === Number(view.acting_player_id) ? ' — to act' : ''}</option>`).join('')}
                        </select>
                    </label>
                    <label class="sb-field">Agent
                        <select data-sb-input="ai-agent">
                            ${AGENTS.map((agent) => `<option value="${agent.id}" ${agent.id === ai.agent ? 'selected' : ''}>${agent.label}</option>`).join('')}
                        </select>
                    </label>
                    ${ai.agent === 'ladder' ? `
                        <label class="sb-field">Elo
                            <input type="number" min="400" max="1400" step="10" value="${ai.elo}" data-sb-input="ai-elo">
                        </label>` : ''}
                </div>
                <div class="sb-group">
                    <button type="button" class="sb-btn" data-sb="analyze">Analyze</button>
                    <button type="button" class="sb-btn" data-sb="ai-move" data-steps="1">Play 1 move</button>
                    <button type="button" class="sb-btn" data-sb="ai-move" data-steps="10">Play turn</button>
                    <button type="button" class="sb-btn ghost" data-sb="play-out">Auto-play match</button>
                </div>
                ${analysis ? `
                    <div class="sb-analysis">
                        <div class="tiny">Every legal action of ${seatName(analysis.player_id)}, scored one ply deep with
                            the AI's own evaluation (position now: ${analysis.baseline}).
                            ${analysis.truncated ? `Showing ${rows.length} of ${analysis.total_actions}.` : ''}</div>
                        ${best ? `<div class="sb-best">Best: ${escapeHtml(best.label)} (${best.delta >= 0 ? '+' : ''}${best.delta})</div>` : ''}
                        <ol class="sb-analysis-list">
                            ${rows.map((row) => `
                                <li>
                                    <button type="button" class="sb-action" data-sb="action" data-kind="${escapeHtml(row.kind)}"
                                        data-player-id="${row.player_id}" data-card-id="${escapeHtml(row.card_id || '')}"
                                        data-location-id="${row.location_id ?? ''}" data-option-id="${escapeHtml(row.option_id || '')}">
                                        ${escapeHtml(row.label)}
                                    </button>
                                    <span class="sb-score ${Number(row.delta) >= 0 ? 'up' : 'down'}">
                                        ${row.error ? escapeHtml(row.error) : `${row.delta >= 0 ? '+' : ''}${row.delta}`}
                                    </span>
                                </li>`).join('')}
                        </ol>
                    </div>` : ''}
            </section>`;
    }

    function renderStepsPanel() {
        const steps = view.steps || [];
        return `
            <section class="sb-panel">
                <header class="sb-panel-head"><h3>Steps <span class="sb-count">${view.step_index + 1}/${steps.length}</span></h3></header>
                <ol class="sb-steps">
                    ${steps.map((step) => `
                        <li>
                            <button type="button" class="sb-step ${step.index === view.step_index ? 'current' : ''} ${step.index > view.step_index ? 'future' : ''}"
                                data-sb="goto" data-index="${step.index}">
                                <span class="sb-step-no">${step.index}</span> ${escapeHtml(step.label)}
                            </button>
                        </li>`).join('')}
                </ol>
            </section>`;
    }

    function renderLogPanel() {
        const history = view.action_history || [];
        const flags = view.flags || {};
        return `
            <section class="sb-panel">
                <header class="sb-panel-head"><h3>Rules log</h3></header>
                <div class="sb-flags">
                    <span class="sb-flag">flood used: ${flags.flood_used ? 'yes' : 'no'}</span>
                    <span class="sb-flag">flood pending turn: ${flags.flood_pending_turn}</span>
                    <span class="sb-flag">beings left world: ${flags.beings_left_world_this_turn ? 'yes' : 'no'}</span>
                </div>
                <ol class="sb-log">
                    ${history.length
                        ? history.slice().reverse().map((entry) => `<li title="${escapeHtml(entry.raw)}">${escapeHtml(entry.text)}</li>`).join('')
                        : '<li class="sb-empty">Nothing has happened yet.</li>'}
                </ol>
            </section>`;
    }

    function renderSide() {
        if (!view) {
            ui.sandboxSide.innerHTML = '';
            return;
        }
        ui.sandboxSide.innerHTML = renderDrivePanel() + renderAiPanel() + renderStepsPanel() + renderLogPanel();
    }

    function render() {
        renderToolbar();
        renderBoard();
        renderSeats();
        renderSide();
        if (cardMenu) renderCardMenu();
        if (picker) renderPicker();
    }

    // --- card menu ---------------------------------------------------------

    function findCard(cardId) {
        for (const seat of (view.seats || [])) {
            for (const zone of ['hand', 'deck', 'underworld', 'set_aside']) {
                const found = (seat[zone] || []).find((c) => c.id === cardId);
                if (found) return { card: found, zone, playerId: seat.player_id, locationId: null };
            }
        }
        for (const loc of (view.locations || [])) {
            for (const [pid, cards] of Object.entries(loc.stacks || {})) {
                const found = (cards || []).find((c) => c.id === cardId);
                if (found) return { card: found, zone: 'location', playerId: Number(pid), locationId: loc.location_id };
            }
        }
        return null;
    }

    function openCardMenu(cardId) {
        const found = findCard(cardId);
        if (!found) return;
        cardMenu = { cardId, seatPlayerId: found.playerId };
        renderCardMenu();
        openSheet(ui.sandboxCardModal);
    }

    function renderCardMenu() {
        const found = findCard(cardMenu.cardId);
        if (!found) {
            closeSheet(ui.sandboxCardModal);
            cardMenu = null;
            return;
        }
        const { card, zone, playerId, locationId } = found;
        const seatPlayerId = cardMenu.seatPlayerId ?? playerId;
        const modifier = (seatOf(card.owner_player_id) || {}).power_modifiers || {};
        const currentMod = Number(modifier[card.id] || 0);
        ui.sandboxCardTitle.textContent = card.name;

        const laneButtons = (view.locations || []).map((loc) => view.players.map((pid) => `
            <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}"
                data-zone="location" data-player-id="${pid}" data-location-id="${loc.location_id}">
                ${escapeHtml(loc.label)} · ${seatName(pid)}
            </button>`).join('')).join('');

        ui.sandboxCardBody.innerHTML = `
            <div class="sb-cardmenu-top">
                <img class="sb-cardmenu-art" src="${cardPngUrl(card.name)}" alt="${escapeHtml(card.name)}"
                    draggable="false" onerror="this.style.display='none';">
                <div class="sb-cardmenu-facts">
                    <div class="sb-cardmenu-line">${escapeHtml(card.type || '')}${card.subtype ? ` — ${escapeHtml(card.subtype)}` : ''}</div>
                    <div class="sb-cardmenu-line">cost ${card.cost ?? '?'} · power ${card.power ?? card.base_power ?? '?'}
                        ${currentMod ? `(${currentMod > 0 ? '+' : ''}${currentMod} modifier)` : ''}</div>
                    <div class="sb-cardmenu-line">now in: ${ZONE_LABELS[zone]}${zone === 'location' ? ` (${view.locations[locationId].label}, ${seatName(playerId)}'s side)` : ` — ${seatName(playerId)}`}</div>
                    <div class="sb-cardmenu-line">owner: ${seatName(card.owner_player_id)}</div>
                    <p class="sb-cardmenu-effect">${escapeHtml(card.effect || 'No effect text')}</p>
                </div>
            </div>
            <div class="sb-cardmenu-section">
                <label class="sb-field">Move to seat
                    <select data-sb-input="cardmenu-seat">
                        ${view.players.map((pid) => `<option value="${pid}" ${Number(pid) === Number(seatPlayerId) ? 'selected' : ''}>${seatName(pid)}</option>`).join('')}
                    </select>
                </label>
                <div class="sb-mini-row">
                    <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}" data-zone="hand"
                        data-player-id="${seatPlayerId}">Hand</button>
                    <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}" data-zone="deck"
                        data-player-id="${seatPlayerId}" data-index="0">Deck (top)</button>
                    <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}" data-zone="deck"
                        data-player-id="${seatPlayerId}">Deck (bottom)</button>
                    <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}" data-zone="underworld"
                        data-player-id="${seatPlayerId}">Underworld</button>
                    <button type="button" class="sb-mini" data-sb="move" data-card-id="${escapeHtml(card.id)}" data-zone="set_aside"
                        data-player-id="${seatPlayerId}">Set aside</button>
                    <button type="button" class="sb-mini danger" data-sb="remove-card" data-card-id="${escapeHtml(card.id)}">Out of the game</button>
                </div>
            </div>
            <div class="sb-cardmenu-section">
                <div class="sb-zone-title">Put in play</div>
                <div class="sb-mini-row">${laneButtons}</div>
            </div>
            <div class="sb-cardmenu-section">
                <div class="sb-mini-row">
                    <label class="sb-field">Power modifier
                        <input type="number" value="${currentMod}" data-sb-input="power-mod" data-card-id="${escapeHtml(card.id)}">
                    </label>
                    <label class="sb-check">
                        <input type="checkbox" data-sb-input="facedown" data-card-id="${escapeHtml(card.id)}" ${card.facedown ? 'checked' : ''}>
                        Face-down
                    </label>
                </div>
            </div>`;
    }

    // --- card picker -------------------------------------------------------

    function openPicker({ zone, playerId, locationId }) {
        picker = { zone, playerId, locationId, query: '' };
        ui.sandboxPickerSearch.value = '';
        renderPicker();
        openSheet(ui.sandboxPickerModal);
        ui.sandboxPickerSearch.focus();
    }

    function renderPicker() {
        const target = picker.zone === 'location'
            ? `${(view.locations[picker.locationId] || {}).label || 'lane'} (${seatName(picker.playerId)}'s side)`
            : `${ZONE_LABELS[picker.zone]} — ${seatName(picker.playerId)}`;
        ui.sandboxPickerTitle.textContent = `Add a card to ${target}`;
        const query = picker.query.trim().toLowerCase();
        const matches = catalog.filter((card) => {
            if (!query) return true;
            return `${card.name} ${card.type} ${card.subtype} ${card.effect}`.toLowerCase().includes(query);
        }).slice(0, 120);
        ui.sandboxPickerList.innerHTML = matches.length
            ? matches.map((card) => `
                <button type="button" class="sb-picker-card" data-sb="pick" data-card-id="${escapeHtml(card.id)}">
                    <img src="${cardPngUrl(card.name)}" alt="" draggable="false" loading="lazy" onerror="this.style.display='none';">
                    <span class="sb-picker-text">
                        <span class="sb-picker-name">${escapeHtml(card.name)}</span>
                        <span class="sb-picker-meta">${escapeHtml(card.type || '')}${card.subtype ? ` — ${escapeHtml(card.subtype)}` : ''} · cost ${card.cost} · power ${card.power}</span>
                        <span class="sb-picker-effect">${escapeHtml(card.effect || '')}</span>
                    </span>
                </button>`).join('')
            : '<div class="sb-empty">No card matches that search.</div>';
    }

    // --- scenario import / export -----------------------------------------

    async function openExport() {
        const data = await postJson('/api/sandbox/export', { match_id: matchId }).catch((error) => {
            showToast(String(error));
            return null;
        });
        if (!data) return;
        ui.sandboxScenarioTitle.textContent = 'Export scenario';
        ui.sandboxScenarioHint.textContent = 'Copy this JSON to file the position in a bug report, or save it for later.';
        ui.sandboxScenarioText.value = JSON.stringify(data.scenario, null, 2);
        ui.sandboxScenarioActions.innerHTML = `
            <button type="button" class="sb-btn" data-sb="copy-scenario">Copy</button>
            <button type="button" class="sb-btn ghost" data-sb="download-scenario">Download</button>`;
        openSheet(ui.sandboxScenarioModal);
    }

    function openImport() {
        ui.sandboxScenarioTitle.textContent = 'Import scenario';
        ui.sandboxScenarioHint.textContent = 'Paste a scenario exported from testing mode. It replaces the current sandbox match.';
        ui.sandboxScenarioText.value = '';
        ui.sandboxScenarioActions.innerHTML = '<button type="button" class="sb-btn primary" data-sb="do-import">Load scenario</button>';
        openSheet(ui.sandboxScenarioModal);
    }

    async function doImport() {
        let scenario;
        try {
            scenario = JSON.parse(ui.sandboxScenarioText.value);
        } catch (error) {
            showToast('That is not valid JSON.');
            return;
        }
        matchId = `sandbox-${Math.floor(Math.random() * 1_000_000)}`;
        analysis = null;
        controlPlayerId = null;
        ai.playerId = null;
        const data = await call('/api/sandbox/import', { scenario });
        if (data) {
            closeSheet(ui.sandboxScenarioModal);
            showToast('Scenario loaded.');
        }
    }

    // --- sheets ------------------------------------------------------------

    function openSheet(modal) {
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeSheet(modal) {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    // --- events ------------------------------------------------------------

    function mutate(op, options) {
        return call('/api/sandbox/mutate', { ops: [op] }, options);
    }

    async function onClick(event) {
        const target = event.target.closest('[data-sb]');
        if (!target) return;
        const kind = target.dataset.sb;
        const cardId = target.dataset.cardId || null;
        const playerId = target.dataset.playerId ? Number(target.dataset.playerId) : null;
        const locationId = target.dataset.locationId === '' || target.dataset.locationId === undefined
            ? null
            : Number(target.dataset.locationId);

        if (kind === 'card') {
            openCardMenu(cardId);
            return;
        }
        if (kind === 'add') {
            openPicker({ zone: target.dataset.zone, playerId, locationId });
            return;
        }
        if (kind === 'pick') {
            const { zone, playerId: seatId, locationId: lane } = picker;
            closeSheet(ui.sandboxPickerModal);
            picker = null;
            await mutate({ op: 'add_card', card_id: cardId, zone, player_id: seatId, location_id: lane });
            return;
        }
        if (kind === 'move') {
            const op = {
                op: 'move_card',
                card_id: cardId,
                zone: target.dataset.zone,
                player_id: playerId,
                location_id: locationId,
            };
            if (target.dataset.index !== undefined) op.index = Number(target.dataset.index);
            closeSheet(ui.sandboxCardModal);
            cardMenu = null;
            await mutate(op);
            return;
        }
        if (kind === 'remove-card') {
            closeSheet(ui.sandboxCardModal);
            cardMenu = null;
            await mutate({ op: 'remove_card', card_id: cardId });
            return;
        }
        if (kind === 'clear-zone') {
            await mutate({ op: 'clear_zone', zone: target.dataset.zone, player_id: playerId });
            return;
        }
        if (kind === 'shuffle') {
            await mutate({ op: 'shuffle_deck', player_id: playerId, seed: Math.floor(Math.random() * 1_000_000) });
            return;
        }
        if (kind === 'clear-choice') {
            await mutate({ op: 'clear_pending_choice' });
            return;
        }
        if (kind === 'action') {
            await call('/api/sandbox/action', {
                player_id: playerId,
                action_kind: target.dataset.kind,
                card_id: cardId || null,
                location_id: locationId,
                option_id: target.dataset.optionId || null,
            });
            return;
        }
        if (kind === 'undo') { await call('/api/sandbox/undo', { steps: 1 }); return; }
        if (kind === 'redo') { await call('/api/sandbox/redo', { steps: 1 }); return; }
        if (kind === 'goto') { await call('/api/sandbox/goto', { index: Number(target.dataset.index) }); return; }
        if (kind === 'reset') { await call('/api/sandbox/reset', {}); return; }
        if (kind === 'analyze') {
            const data = await postJson('/api/sandbox/analyze', { match_id: matchId, player_id: ai.playerId })
                .catch((error) => { showToast(String(error)); return null; });
            if (data) {
                analysis = data.analysis;
                render();
            }
            return;
        }
        if (kind === 'ai-move') {
            const data = await call('/api/sandbox/ai-move', {
                player_id: ai.playerId,
                agent: ai.agent,
                elo: ai.agent === 'ladder' ? Number(ai.elo) : null,
                steps: Number(target.dataset.steps || 1),
            });
            // The AI only ever moves for the seat the engine is waiting on, so
            // say so rather than looking like the button did nothing.
            if (data && !(data.played || []).length) {
                showToast(`${seatName(ai.playerId)} is not the seat to act right now.`);
            }
            return;
        }
        if (kind === 'play-out') {
            const data = await call('/api/sandbox/play-out', {
                agent: ai.agent,
                elo: ai.agent === 'ladder' ? Number(ai.elo) : null,
            });
            if (data) showToast(`Played ${(data.played || []).length} actions.`);
            return;
        }
        if (kind === 'toggle-setup') { setup.open = !setup.open; render(); return; }
        if (kind === 'create') { await createMatch(); return; }
        if (kind === 'export') { await openExport(); return; }
        if (kind === 'import') { openImport(); return; }
        if (kind === 'do-import') { await doImport(); return; }
        if (kind === 'copy-scenario') {
            ui.sandboxScenarioText.select();
            try {
                await navigator.clipboard.writeText(ui.sandboxScenarioText.value);
                showToast('Scenario copied.');
            } catch (error) {
                showToast('Copy failed — select the text and copy manually.');
            }
            return;
        }
        if (kind === 'download-scenario') {
            const blob = new Blob([ui.sandboxScenarioText.value], { type: 'application/json' });
            const link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = `${matchId}.json`;
            link.click();
            URL.revokeObjectURL(link.href);
        }
    }

    async function onChange(event) {
        const target = event.target.closest('[data-sb-input]');
        if (!target) return;
        const kind = target.dataset.sbInput;
        const playerId = target.dataset.playerId ? Number(target.dataset.playerId) : null;

        if (kind === 'setup-players') {
            setup.players = Number(target.value);
            while (setup.decks.length < setup.players) setup.decks.push(deckOptions[setup.decks.length % deckOptions.length] || '');
            render();
            return;
        }
        if (kind === 'setup-deck') { setup.decks[Number(target.dataset.seat)] = target.value; return; }
        if (kind === 'setup-seed') { setup.seed = Number(target.value) || 0; return; }
        if (kind === 'setup-mulligan') { setup.skipMulligan = target.checked; return; }
        if (kind === 'control-seat') { controlPlayerId = Number(target.value); render(); return; }
        if (kind === 'ai-seat') { ai.playerId = Number(target.value); analysis = null; render(); return; }
        if (kind === 'ai-agent') { ai.agent = target.value; render(); return; }
        if (kind === 'ai-elo') { ai.elo = Number(target.value); return; }
        if (kind === 'cardmenu-seat') {
            cardMenu.seatPlayerId = Number(target.value);
            renderCardMenu();
            return;
        }
        if (kind === 'stat') {
            await mutate({ op: 'set_stat', stat: target.dataset.stat, player_id: playerId, value: Number(target.value) });
            return;
        }
        if (kind === 'phase') { await mutate({ op: 'set_phase', value: target.value }); return; }
        if (kind === 'current') { await mutate({ op: 'set_current_player', player_id: Number(target.value) }); return; }
        if (kind === 'counter') {
            await mutate({ op: 'set_counter', counter: target.dataset.counter, value: Number(target.value) });
            return;
        }
        if (kind === 'power-mod') {
            await mutate({ op: 'set_power_modifier', card_id: target.dataset.cardId, value: Number(target.value) });
            return;
        }
        if (kind === 'facedown') {
            await mutate({ op: 'set_facedown', card_id: target.dataset.cardId, value: target.checked });
        }
    }

    function init(options = {}) {
        onExit = options.onExit || null;
        ui.sandboxScreen.addEventListener('click', onClick);
        ui.sandboxScreen.addEventListener('change', onChange);
        [ui.sandboxCardModal, ui.sandboxPickerModal, ui.sandboxScenarioModal].forEach((modal) => {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) closeSheet(modal);
            });
            modal.addEventListener('click', onClick);
            modal.addEventListener('change', onChange);
        });
        ui.btnCloseSandboxCard.onclick = () => { cardMenu = null; closeSheet(ui.sandboxCardModal); };
        ui.btnCloseSandboxPicker.onclick = () => { picker = null; closeSheet(ui.sandboxPickerModal); };
        ui.btnCloseSandboxScenario.onclick = () => closeSheet(ui.sandboxScenarioModal);
        ui.sandboxPickerSearch.addEventListener('input', () => {
            picker.query = ui.sandboxPickerSearch.value;
            renderPicker();
        });
        if (ui.btnSandboxBack) {
            ui.btnSandboxBack.onclick = () => {
                if (onExit) onExit();
            };
        }
    }

    return { init, open };
}
