// The Replays screens: the saved-replay library, and the player that steps
// through a recording.
//
// The player is the game screen. A recorded step is turned into the same
// snapshot a live match produces (js/replaysnapshot.js) and handed to the same
// renderer (js/render.js), so a replay reads exactly like the match it came
// from — same lanes, same hand, same crowns and mana — instead of a private
// summary of it. What the replay adds is the transport bar, the seat switch,
// and the fact that nothing on the board can be touched.
//
// The board still draws from the replay file alone: its recorded states and
// the card printings it bundled. The live catalog and the rules engine are
// deliberately not consulted, which is what lets a replay from an older build
// show that build's cards and that build's numbers.

import { escapeHtml, showToast } from './helpers.js';
import { layoutHand, renderSnapshot } from './render.js';
import {
    ReplayError,
    deleteReplay,
    describeOption,
    exportReplay,
    expandFrames,
    formatAction,
    getReplay,
    listReplays,
    parseReplayText,
    readFileText,
    replayCard,
    saveReplay,
    summarizeReplay,
} from './replay.js';
import { replaySnapshot, seatNames } from './replaysnapshot.js';

// Auto-play pacing. Each press of the speed button moves one step down.
const SPEEDS = [1, 2, 4, 0.5];
const BASE_STEP_MS = 900;

// The render pipeline writes into a handful of elements the replay screen has
// no use for (the deck pickers, the End Turn button, the live choice modal).
// They get detached stand-ins so nothing has to be special-cased in render.js
// and nothing can leak onto the game screen.
function stub(tag) {
    return document.createElement(tag);
}

export function createReplayScreens(ui, { cardStack, onOpenPlayer }) {
    // The replay currently loaded into the player, its expanded steps, and
    // where the playhead is.
    let replay = null;
    let steps = [];
    let position = 0;
    let timer = null;
    let speedIdx = 0;
    // Whose side of the table we watch from. The board is egocentric, so this
    // is what decides which hand is face-up in front of you.
    let viewerSeat = 0;

    // The board's own element map: the game screen's names pointing at the
    // replay screen's copies. Built once — the elements never change.
    const boardUi = {
        gameScreen: ui.replayScreen,
        scorePanel: ui.rpScorePanel,
        oppChips: ui.rpOppChips,
        laneDots: ui.rpLaneDots,
        hud: ui.rpHud,
        pending: stub('div'),
        status: ui.rpStatus,
        lanes: ui.rpLanes,
        hand: ui.rpHand,
        yourMana: ui.rpYourMana,
        oppMana: ui.rpOppMana,
        oppHand: ui.rpOppHand,
        oppHandCount: ui.rpOppHandCount,
        yourUnderworld: ui.rpYourUnderworld,
        oppUnderworld: ui.rpOppUnderworld,
        yourUnderworldCount: ui.rpYourUnderworldCount,
        oppUnderworldCount: ui.rpOppUnderworldCount,
        yourDeckCount: ui.rpYourDeckCount,
        oppDeckCount: ui.rpOppDeckCount,
        yourDeckStack: ui.rpYourDeckStack,
        oppDeckStack: ui.rpOppDeckStack,
        actionHistory: ui.replayActionHistory,
        // Nothing to pick, nothing to start, nothing to end.
        btnEndTurn: stub('button'),
        deckA: stub('select'),
        deckB: stub('select'),
        checkpointPath: stub('select'),
        checkpointField: stub('div'),
        choiceModal: stub('div'),
        choiceTitle: stub('div'),
        choicePrompt: stub('div'),
        choiceSub: stub('div'),
        choiceOptions: stub('div'),
    };

    // The render pipeline's scratch state. Private to the replay, so watching
    // one can never disturb a match left running underneath.
    const boardApp = {
        snapshot: null,
        cardNameById: new Map(),
        mulliganSelected: new Set(),
        playableCardSet: new Set(),
        abilityReadyCardSet: new Set(),
        legalMoveChoiceSet: new Set(),
        movableChoiceCardSet: new Set(),
        opponentTurnActive: false,
        sandboxToolsHidden: true,
        lanesScrollMatchId: null,
        playerElo: null,
        aiElos: {},
    };

    // --- Seat naming ------------------------------------------------------

    function seatIdxOf(playerId) {
        const ids = (replay && replay.player_ids) || [];
        const idx = ids.indexOf(Number(playerId));
        return idx >= 0 ? idx : 0;
    }

    function seatLabels() {
        return replay ? seatNames(replay) : [];
    }

    function seatLabel(seatIdx) {
        return seatLabels()[seatIdx] || `Player ${seatIdx + 1}`;
    }

    const namePlayer = (playerId) => seatLabel(seatIdxOf(playerId));

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
        // Open on the seat the recording was taken from, so a player watching
        // their own match sits where they sat.
        const meta = loaded.client_meta || {};
        viewerSeat = Math.max(0, (loaded.player_ids || []).indexOf(Number(meta.viewer_player_id)));
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

    // --- The board --------------------------------------------------------

    function render() {
        if (!replay) return;
        const step = current();
        const state = step.state || {};
        const snapshot = replaySnapshot(replay, step, viewerSeat);
        const names = seatLabels();
        const config = {
            player_id: Number((replay.player_ids || [])[viewerSeat]),
            // A recording is watched from the outside, so every seat is named
            // rather than split into "You" and "Opp" — the same way a
            // pass-and-play match names the humans sharing the screen.
            local_seat_ids: (replay.player_ids || []).map(Number),
            local_seat_names: names,
        };

        // What the watched seat had marked for redraw, so a recorded mulligan
        // shows its red X marks.
        boardApp.mulliganSelected = new Set((state.mulligan_selected || [])[viewerSeat] || []);
        renderSnapshot({
            snapshot,
            ui: boardUi,
            app: boardApp,
            config,
            // Reachable only from the live choice modal, which the replay
            // never opens (see boardUi).
            onChooseOption: () => {},
            cardStack: null,
        });
        layoutHand(boardUi);

        markStepCard(step);
        renderChoice(step);
        renderCaption(step);
        renderTransport();
        if (ui.rpSeatName) ui.rpSeatName.textContent = seatLabel(viewerSeat);
        if (ui.btnReplaySeat) ui.btnReplaySeat.disabled = seatCount() < 2;
    }

    // The card this step acted on, lit the way the board lights a card it is
    // asking about — the one thing a still frame can't say for itself.
    function markStepCard(step) {
        const cardId = (step.action && step.action.card_id) || null;
        if (!cardId) return;
        const selector = `[data-board-card-id="${CSS.escape(cardId)}"], .hand-card[data-card-id="${CSS.escape(cardId)}"]`;
        for (const el of ui.replayScreen.querySelectorAll(selector)) {
            el.classList.add('rp-hot');
        }
    }

    // The decision a seat faced at this step, with the option they went on to
    // take marked. A live game asks this in a modal; a recording has the
    // answer already, so it reads inline.
    function renderChoice(step) {
        const pending = (step.state || {}).pending_choice;
        if (!pending) {
            ui.rpChoice.innerHTML = '';
            return;
        }
        const next = steps[position + 1];
        const taken = next && next.action && next.action.kind === 'choose_option'
            ? String(next.action.option_id)
            : null;
        const options = (pending.options || []).map((option) => {
            const chosen = String(option) === taken;
            return `<span class="rp-choice-option ${chosen ? 'rp-choice-taken' : ''}">${escapeHtml(describeOption(replay, option))}</span>`;
        }).join('');
        // Laid out like the live choice sheet — who it is for, what it asked,
        // then the options — so the same decision reads the same way.
        ui.rpChoice.innerHTML = `
            <div class="rp-choice">
                <div class="rp-choice-who">Choice for <strong>${escapeHtml(namePlayer(pending.player_id))}</strong></div>
                <div class="rp-choice-prompt">${escapeHtml(pending.prompt || pending.choice_kind || '')}</div>
                <div class="rp-choice-options">${options}</div>
            </div>
        `;
    }

    // Where a live board offers End Turn, the replay says what this step was.
    function renderCaption(step) {
        const text = formatAction(replay, step.action, namePlayer);
        const fresh = (step.newLog || []).length;
        ui.rpAction.innerHTML = text
            ? `<span class="rp-action-text">${escapeHtml(text)}</span>`
            : `<span class="rp-action-text rp-action-quiet">${escapeHtml(position === 0 ? 'The opening hands are dealt' : 'The board settles')}</span>`;
        ui.rpAction.classList.toggle('rp-action-live', fresh > 0);
    }

    function renderTransport() {
        ui.replayStepLabel.textContent = `Step ${position + 1} / ${steps.length}`;
        ui.replayRange.value = String(position);
        ui.btnReplayPlay.textContent = timer ? '⏸' : '▶';
        ui.btnReplayPlay.setAttribute('aria-label', timer ? 'Pause' : 'Play');
        ui.btnReplaySpeed.textContent = `${SPEEDS[speedIdx]}×`;
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

    // Leaving the screen must not leave a timer redrawing a hidden board, and
    // must not leave the history sheet open over whatever comes next.
    function close() {
        stop();
        closeHistory();
    }

    function switchSeat() {
        if (seatCount() < 2) return;
        viewerSeat = (viewerSeat + 1) % seatCount();
        render();
    }

    function openHistory() {
        ui.replayHistoryModal.classList.add('open');
        ui.replayHistoryModal.setAttribute('aria-hidden', 'false');
    }

    function closeHistory() {
        ui.replayHistoryModal.classList.remove('open');
        ui.replayHistoryModal.setAttribute('aria-hidden', 'true');
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

        ui.btnReplaySeat.addEventListener('click', switchSeat);
        ui.btnReplayHistory.addEventListener('click', openHistory);
        ui.btnCloseReplayHistory.addEventListener('click', closeHistory);
        ui.replayHistoryModal.addEventListener('click', (event) => {
            if (event.target === ui.replayHistoryModal) closeHistory();
        });

        // The board is a recording, so a card can only be looked at — never
        // dragged, played or targeted. Board cards carry the game's
        // data-board-card-id, hand cards the game's data-card-id.
        ui.replayScreen.addEventListener('click', (event) => {
            const tile = event.target.closest('[data-board-card-id], .hand-card[data-card-id]');
            if (!tile) return;
            openCard(tile.getAttribute('data-board-card-id') || tile.getAttribute('data-card-id'));
        });
    }

    return { init, renderLibrary, load, close };
}
