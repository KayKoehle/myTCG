import { postJson } from './api.js';
import { actionLabel, cardDisplayName, humanLegalActions, laneLabel } from './helpers.js';
import { renderSnapshot, layoutHand } from './render.js';
import { buildConfig, createAppState } from './state.js';

export function createGameController(ui) {
    const app = createAppState();

    const cfg = () => buildConfig(ui, app);

    function rerender(snapshot) {
        renderSnapshot({
            snapshot,
            ui,
            app,
            config: cfg(),
            onChooseOption: (optionId) => {
                doAction({ kind: 'choose_option', option_id: optionId });
            },
        });
        bindDragAndDrop(snapshot);
        bindBoardMoveChoices(snapshot);
        bindMulliganSelection(snapshot);
        layoutHand(ui);
        maybeAutoAdvance();
    }

    function isOpeningMulligan(snapshot) {
        return snapshot.phase === 'MULLIGAN'
            && snapshot.pending_choice
            && snapshot.pending_choice.choice_kind === 'opening_mulligan';
    }

    function bindDragAndDrop(snapshot) {
        const legal = humanLegalActions(snapshot, cfg().player_id);
        app.legalPlaySet = new Set(
            legal
                .filter((a) => a.kind === 'play_card' && a.card_id != null && a.location_id != null)
                .map((a) => `${a.card_id}|${a.location_id}`)
        );
        app.playableCardSet = new Set(
            legal
                .filter((a) => a.kind === 'play_card' && a.card_id != null)
                .map((a) => a.card_id)
        );

        if (isOpeningMulligan(snapshot)) return;

        const cards = ui.hand.querySelectorAll('.hand-card[data-card-id]');
        cards.forEach((cardEl) => {
            const cardId = cardEl.dataset.cardId || '';
            const isPlayable = app.playableCardSet.has(cardId);
            cardEl.setAttribute('draggable', isPlayable ? 'true' : 'false');

            cardEl.addEventListener('dragstart', (event) => {
                if (!isPlayable) {
                    event.preventDefault();
                    return;
                }
                app.draggedCardId = cardEl.dataset.cardId;
                cardEl.classList.add('dragging');
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', app.draggedCardId || '');
                }
            });

            cardEl.addEventListener('dragend', () => {
                cardEl.classList.remove('dragging');
                app.draggedCardId = null;
                document.querySelectorAll('.lane-row.drop-target').forEach((el) => el.classList.remove('drop-target'));
            });
        });

        const zones = ui.lanes.querySelectorAll('.lane-row.lane-drop[data-location-id]');
        zones.forEach((zone) => {
            const loc = Number(zone.dataset.locationId);
            const canAnyDrop = snapshot.hand.some((c) => app.legalPlaySet.has(`${c.id}|${loc}`));
            if (canAnyDrop) zone.classList.add('can-drop');

            zone.addEventListener('dragover', (event) => {
                const payload = event.dataTransfer ? event.dataTransfer.getData('text/plain') : '';
                const moveCardId = payload.startsWith('MOVE:') ? payload.slice(5) : '';
                const activeCardId = app.draggedCardId || payload;
                const canPlayDrop = Boolean(activeCardId && app.legalPlaySet.has(`${activeCardId}|${loc}`));
                const canMoveDrop = Boolean(moveCardId && app.legalMoveChoiceSet.has(`${moveCardId}|${loc}`));
                if (canPlayDrop || canMoveDrop) {
                    event.preventDefault();
                    zone.classList.add('drop-target');
                    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
                }
            });

            zone.addEventListener('dragleave', () => {
                zone.classList.remove('drop-target');
            });

            zone.addEventListener('drop', async (event) => {
                event.preventDefault();
                zone.classList.remove('drop-target');
                const payload = event.dataTransfer ? event.dataTransfer.getData('text/plain') : '';
                const moveCardId = payload.startsWith('MOVE:') ? payload.slice(5) : '';
                if (moveCardId) {
                    const pending = snapshot.pending_choice;
                    if (!pending) return;
                    const option = (pending.options || []).find((opt) => {
                        if (typeof opt !== 'string') return false;
                        const parts = opt.split('|');
                        return parts.length === 3 && parts[0] === moveCardId && Number(parts[1]) === loc;
                    });
                    if (!option) {
                        ui.status.textContent = `Cannot move ${cardDisplayName(moveCardId, app.cardNameById)} to ${laneLabel(loc)} right now.`;
                        return;
                    }
                    await doAction({ kind: 'choose_option', option_id: option });
                    return;
                }

                const activeCardId = app.draggedCardId || payload;
                if (!activeCardId) return;
                if (!app.legalPlaySet.has(`${activeCardId}|${loc}`)) {
                    ui.status.textContent = `Cannot play ${cardDisplayName(activeCardId, app.cardNameById)} to ${laneLabel(loc)} right now.`;
                    return;
                }
                await doAction({ kind: 'play_card', card_id: activeCardId, location_id: loc });
            });
        });
    }

    function bindBoardMoveChoices(snapshot) {
        const pending = snapshot.pending_choice;
        if (!pending || Number(pending.player_id) !== cfg().player_id || app.legalMoveChoiceSet.size === 0) return;

        const boardCards = ui.lanes.querySelectorAll('.card.movable-choice[data-board-card-id]');
        boardCards.forEach((cardEl) => {
            const cardId = cardEl.dataset.boardCardId;
            if (!cardId) return;
            cardEl.setAttribute('draggable', 'true');

            cardEl.addEventListener('dragstart', (event) => {
                cardEl.classList.add('dragging');
                if (event.dataTransfer) {
                    event.dataTransfer.effectAllowed = 'move';
                    event.dataTransfer.setData('text/plain', `MOVE:${cardId}`);
                }
            });

            cardEl.addEventListener('dragend', () => {
                cardEl.classList.remove('dragging');
                document.querySelectorAll('.lane-row.drop-target').forEach((el) => el.classList.remove('drop-target'));
            });
        });
    }

    function bindMulliganSelection(snapshot) {
        const openingMulligan = isOpeningMulligan(snapshot);
        const canActMulligan = openingMulligan && Number(snapshot.pending_choice.player_id) === cfg().player_id;
        if (!openingMulligan || !canActMulligan) return;

        ui.hand.querySelectorAll('.hand-card[data-card-id]').forEach((cardEl) => {
            cardEl.addEventListener('click', () => {
                const cardId = cardEl.dataset.cardId;
                if (!cardId) return;
                if (app.mulliganSelected.has(cardId)) {
                    app.mulliganSelected.delete(cardId);
                    cardEl.classList.remove('marked');
                } else {
                    app.mulliganSelected.add(cardId);
                    cardEl.classList.add('marked');
                }
                ui.mulliganInfo.textContent = `Selected: ${app.mulliganSelected.size}. Click cards with red X, then press Confirm mulligan.`;
            });
        });
    }

    async function refresh() {
        const c = cfg();
        const data = await postJson('/api/state', {
            match_id: c.match_id,
            player_id: c.player_id,
            seed: c.seed,
            deck_a: c.deck_a,
            deck_b: c.deck_b,
        });
        rerender(data.snapshot);
    }

    async function doAction(action) {
        try {
            const c = cfg();
            const data = await postJson('/api/action', {
                match_id: c.match_id,
                player_id: c.player_id,
                action_kind: action.kind,
                card_id: action.card_id || null,
                location_id: action.location_id ?? null,
                option_id: action.option_id || null,
                seed: c.seed,
                deck_a: c.deck_a,
                deck_b: c.deck_b,
            });
            rerender(data.snapshot);
        } catch (error) {
            ui.status.textContent = String(error);
        }
    }

    async function aiMove() {
        try {
            const c = cfg();
            const data = await postJson('/api/ai-move', {
                match_id: c.match_id,
                ai_player_id: c.ai_player_id,
                viewer_player_id: c.viewer_player_id,
                seed: c.seed,
                deck_a: c.deck_a,
                deck_b: c.deck_b,
                checkpoint_path: c.checkpoint_path,
                device: c.device,
            });
            rerender(data.snapshot);
            const snap = data.snapshot || {};
            if (!isOpeningMulligan(snap) && ui.status.textContent) {
                ui.status.textContent = `AI action: ${actionLabel(data.action, app.cardNameById)} | ${ui.status.textContent}`;
            }
        } catch (error) {
            ui.status.textContent = String(error);
        }
    }

    async function confirmMulligan() {
        if (!app.snapshot || !app.snapshot.pending_choice) return;
        const pending = app.snapshot.pending_choice;
        if (app.snapshot.phase !== 'MULLIGAN' || pending.choice_kind !== 'opening_mulligan') return;
        if (Number(pending.player_id) !== cfg().player_id) return;

        const selectable = new Set((pending.options || []).filter((x) => x !== 'KEEP'));
        const picked = Array.from(app.mulliganSelected).filter((id) => selectable.has(id));
        for (const cardId of picked) {
            await doAction({ kind: 'choose_option', option_id: cardId });
        }
        await doAction({ kind: 'choose_option', option_id: 'KEEP' });
        app.mulliganSelected.clear();
    }

    async function maybeAutoAdvance() {
        if (app.autoRunning) return;
        app.autoRunning = true;
        try {
            for (let i = 0; i < 40; i += 1) {
                const snap = app.snapshot;
                if (!snap || snap.phase === 'GAME_OVER') return;
                const c = cfg();
                const current = Number(snap.current_player_id);
                const pendingForAi = Boolean(
                    snap.pending_choice
                    && Number(snap.pending_choice.player_id) === c.ai_player_id
                );
                if (current === c.ai_player_id || pendingForAi) {
                    await aiMove();
                    continue;
                }
                const drawAction = humanLegalActions(snap, c.player_id).find((a) => a.kind === 'draw_card');
                if (drawAction) {
                    await doAction(drawAction);
                    continue;
                }
                return;
            }
        } finally {
            app.autoRunning = false;
        }
    }

    async function onEndTurn() {
        const snap = app.snapshot;
        if (!snap) return;

        if (snap.phase === 'GAME_OVER') {
            await newGame();
            return;
        }

        if (isOpeningMulligan(snap) && Number(snap.pending_choice.player_id) === cfg().player_id) {
            await confirmMulligan();
            return;
        }

        const action = humanLegalActions(snap, cfg().player_id).find((a) => a.kind === 'end_turn');
        if (action) {
            await doAction(action);
        }
    }

    async function newGame() {
        app.matchId = `snap-match-${Math.floor(Math.random() * 1_000_000)}`;
        app.seed = Math.floor(Math.random() * 1_000_000_000);
        app.mulliganSelected.clear();
        await refresh();
    }

    function init() {
        ui.btnNewGame.onclick = () => {
            newGame();
        };
        ui.btnEndTurn.onclick = () => {
            onEndTurn();
        };
        window.addEventListener('resize', () => {
            layoutHand(ui);
        });

        refresh().catch((error) => {
            ui.status.textContent = String(error);
        });
    }

    return {
        init,
    };
}
