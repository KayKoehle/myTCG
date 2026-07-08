import { postJson } from './api.js';
import { actionLabel, cardArtTag, cardDisplayName, effectLabel, escapeHtml, findCardById, humanLegalActions, laneLabel, typeLabel } from './helpers.js';
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
        app.selectedCardId = null;
        bindDragAndDrop(snapshot);
        bindBoardMoveChoices(snapshot);
        bindMulliganSelection(snapshot);
        bindTapControls(snapshot);
        layoutHand(ui);
        maybeAutoAdvance();
    }

    function isOpeningMulligan(snapshot) {
        return snapshot.phase === 'MULLIGAN'
            && snapshot.pending_choice
            && snapshot.pending_choice.choice_kind === 'opening_mulligan';
    }

    // Custom pointer-based dragging: HTML5 drag-and-drop does not work on
    // touch screens, so hand cards and movable board cards drag via pointer
    // events (mouse, touch, and pen) with a floating ghost element.
    function laneAtPoint(clientX, clientY) {
        const hit = document.elementFromPoint(clientX, clientY);
        if (!hit) return null;
        const lane = hit.closest('.lane');
        if (!lane || !ui.lanes.contains(lane)) return null;
        const zone = lane.querySelector('.lane-row.lane-drop[data-location-id]');
        return zone ? Number(zone.dataset.locationId) : null;
    }

    function highlightLane(loc) {
        ui.lanes.querySelectorAll('.lane-row.drop-target').forEach((el) => el.classList.remove('drop-target'));
        if (loc === null) return;
        const zone = ui.lanes.querySelector(`.lane-row.lane-drop[data-location-id="${loc}"]`);
        if (zone) zone.classList.add('drop-target');
    }

    function beginPointerDrag(event, cardEl, payload) {
        if (event.button !== undefined && event.button !== 0) return;
        const pointerId = event.pointerId;
        const startX = event.clientX;
        const startY = event.clientY;
        let dragging = false;
        let ghost = null;

        const legalLane = (loc) => (payload.type === 'play'
            ? app.legalPlaySet.has(`${payload.cardId}|${loc}`)
            : app.legalMoveChoiceSet.has(`${payload.cardId}|${loc}`));

        const cleanup = () => {
            cardEl.removeEventListener('pointermove', onMove);
            cardEl.removeEventListener('pointerup', onUp);
            cardEl.removeEventListener('pointercancel', onCancel);
            if (ghost) ghost.remove();
            cardEl.classList.remove('dragging');
            highlightLane(null);
        };

        const onMove = (moveEvent) => {
            const dx = moveEvent.clientX - startX;
            const dy = moveEvent.clientY - startY;
            if (!dragging) {
                if (Math.hypot(dx, dy) < 9) return;
                dragging = true;
                clearTapSelection();
                const rect = cardEl.getBoundingClientRect();
                ghost = cardEl.cloneNode(true);
                ghost.classList.remove('selected', 'playable', 'unplayable', 'movable-choice');
                ghost.classList.add('drag-ghost');
                ghost.style.width = `${rect.width}px`;
                ghost.style.height = `${rect.height}px`;
                document.body.appendChild(ghost);
                cardEl.classList.add('dragging');
            }
            ghost.style.left = `${moveEvent.clientX}px`;
            ghost.style.top = `${moveEvent.clientY}px`;
            const loc = laneAtPoint(moveEvent.clientX, moveEvent.clientY);
            highlightLane(loc !== null && legalLane(loc) ? loc : null);
            moveEvent.preventDefault();
        };

        const onUp = async (upEvent) => {
            const wasDragging = dragging;
            cleanup();
            if (!wasDragging) return; // Plain tap: the click handlers take it.
            app.dragEndedAt = Date.now();
            const loc = laneAtPoint(upEvent.clientX, upEvent.clientY);
            if (loc === null) return;
            if (!legalLane(loc)) {
                const verb = payload.type === 'play' ? 'play' : 'move';
                ui.status.textContent = `Cannot ${verb} ${cardDisplayName(payload.cardId, app.cardNameById)} to ${laneLabel(loc)} right now.`;
                return;
            }
            if (payload.type === 'play') {
                await doAction({ kind: 'play_card', card_id: payload.cardId, location_id: loc });
                return;
            }
            const pending = app.snapshot && app.snapshot.pending_choice;
            if (!pending) return;
            const option = (pending.options || []).find((opt) => {
                if (typeof opt !== 'string') return false;
                const parts = opt.split('|');
                return parts.length === 3 && parts[0] === payload.cardId && Number(parts[1]) === loc;
            });
            if (option) await doAction({ kind: 'choose_option', option_id: option });
        };

        const onCancel = () => cleanup();

        try {
            cardEl.setPointerCapture(pointerId);
        } catch (error) {
            // Pointer capture is best-effort; dragging still works without it.
        }
        cardEl.addEventListener('pointermove', onMove);
        cardEl.addEventListener('pointerup', onUp);
        cardEl.addEventListener('pointercancel', onCancel);
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

        ui.hand.querySelectorAll('.hand-card[data-card-id]').forEach((cardEl) => {
            const cardId = cardEl.dataset.cardId || '';
            if (!app.playableCardSet.has(cardId)) return;
            cardEl.classList.add('draggable');
            cardEl.addEventListener('pointerdown', (event) => {
                beginPointerDrag(event, cardEl, { type: 'play', cardId });
            });
        });

        ui.lanes.querySelectorAll('.lane-row.lane-drop[data-location-id]').forEach((zone) => {
            const loc = Number(zone.dataset.locationId);
            const canAnyDrop = snapshot.hand.some((c) => app.legalPlaySet.has(`${c.id}|${loc}`));
            if (canAnyDrop) zone.classList.add('can-drop');
        });
    }

    function bindBoardMoveChoices(snapshot) {
        const pending = snapshot.pending_choice;
        if (!pending || Number(pending.player_id) !== cfg().player_id || app.legalMoveChoiceSet.size === 0) return;

        ui.lanes.querySelectorAll('.card.movable-choice[data-board-card-id]').forEach((cardEl) => {
            const cardId = cardEl.dataset.boardCardId;
            if (!cardId) return;
            cardEl.addEventListener('pointerdown', (event) => {
                beginPointerDrag(event, cardEl, { type: 'move', cardId });
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
                ui.mulliganInfo.textContent = `Selected: ${app.mulliganSelected.size}. Tap cards to mark them with a red X, then press Confirm mulligan.`;
            });
        });
    }

    function clearTapSelection() {
        app.selectedCardId = null;
        ui.hand.querySelectorAll('.hand-card.selected').forEach((el) => el.classList.remove('selected'));
        ui.lanes.querySelectorAll('.lane-row.tap-target').forEach((el) => el.classList.remove('tap-target'));
    }

    function selectHandCard(cardEl, cardId) {
        clearTapSelection();
        app.selectedCardId = cardId;
        cardEl.classList.add('selected');
        ui.lanes.querySelectorAll('.lane-row.lane-drop[data-location-id]').forEach((zone) => {
            const loc = Number(zone.dataset.locationId);
            if (app.legalPlaySet.has(`${cardId}|${loc}`)) {
                zone.classList.add('tap-target');
            }
        });
        ui.status.textContent = `Tap a highlighted lane to play ${cardDisplayName(cardId, app.cardNameById)}.`;
    }

    function openInspector(card) {
        if (!card || !card.name) return;
        ui.inspectorTitle.textContent = card.name;
        ui.inspectorType.textContent = typeLabel(card);
        ui.inspectorCost.textContent = card.cost ?? '?';
        ui.inspectorPower.textContent = card.power !== null && card.power !== undefined ? card.power : '?';
        ui.inspectorMedia.innerHTML = cardArtTag(card.name, 'inspector-art');
        ui.inspectorEffect.textContent = effectLabel(card);
        ui.cardInspector.classList.add('open');
        ui.cardInspector.setAttribute('aria-hidden', 'false');
    }

    function closeInspector() {
        ui.cardInspector.classList.remove('open');
        ui.cardInspector.setAttribute('aria-hidden', 'true');
    }

    // Tap-first controls: touch devices cannot use HTML5 drag-and-drop or
    // hover, so playable cards select on tap and lanes confirm the play,
    // while any other card opens the full-size inspector.
    function bindTapControls(snapshot) {
        const clickFollowsDrag = () => Date.now() - (app.dragEndedAt || 0) < 350;

        if (!isOpeningMulligan(snapshot)) {
            ui.hand.querySelectorAll('.hand-card[data-card-id]').forEach((cardEl) => {
                cardEl.addEventListener('click', (event) => {
                    event.stopPropagation();
                    if (clickFollowsDrag()) return;
                    const cardId = cardEl.dataset.cardId;
                    if (!cardId) return;
                    if (!app.playableCardSet.has(cardId)) {
                        openInspector(findCardById(snapshot, cardId));
                        return;
                    }
                    if (app.selectedCardId === cardId) {
                        clearTapSelection();
                        return;
                    }
                    selectHandCard(cardEl, cardId);
                });
            });
        }

        ui.lanes.querySelectorAll('.card[data-board-card-id]').forEach((cardEl) => {
            cardEl.addEventListener('click', (event) => {
                // With a hand card selected, a tap on a lane's stack counts as
                // playing to that lane; the lane handler takes it.
                if (app.selectedCardId) return;
                event.stopPropagation();
                openInspector(findCardById(snapshot, cardEl.dataset.boardCardId));
            });
        });

        ui.oppHand.querySelectorAll('.opp-card-revealed').forEach((imgEl) => {
            imgEl.addEventListener('click', (event) => {
                event.stopPropagation();
                const card = (snapshot.opponent_hand || []).find((c) => c && c.name === imgEl.alt);
                openInspector(card);
            });
        });

        ui.lanes.querySelectorAll('.lane').forEach((laneEl) => {
            const zone = laneEl.querySelector('.lane-row.lane-drop[data-location-id]');
            if (!zone) return;
            laneEl.addEventListener('click', async () => {
                if (clickFollowsDrag()) return;
                const cardId = app.selectedCardId;
                if (!cardId) return;
                const loc = Number(zone.dataset.locationId);
                if (!app.legalPlaySet.has(`${cardId}|${loc}`)) {
                    ui.status.textContent = `Cannot play ${cardDisplayName(cardId, app.cardNameById)} to ${laneLabel(loc)} right now.`;
                    return;
                }
                clearTapSelection();
                await doAction({ kind: 'play_card', card_id: cardId, location_id: loc });
            });
        });
    }

    // Fly a copy of the played card from the source rect onto its new spot on
    // the board. Opponent plays start face down and flip up mid-flight.
    function animateCardPlay(cardId, fromRect, { flip = false } = {}) {
        if (!cardId || !fromRect || !window.Element.prototype.animate) return;
        const target = ui.lanes.querySelector(`.card[data-board-card-id="${CSS.escape(cardId)}"]`);
        if (!target) return;
        const endRect = target.getBoundingClientRect();
        if (!endRect.width) return;

        const fly = document.createElement('div');
        fly.className = 'fly-card';
        fly.style.left = `${endRect.left}px`;
        fly.style.top = `${endRect.top}px`;
        fly.style.width = `${endRect.width}px`;
        fly.style.height = `${endRect.height}px`;

        const inner = document.createElement('div');
        inner.className = 'fly-inner';
        const front = document.createElement('div');
        front.className = 'fly-face fly-front';
        front.appendChild(target.cloneNode(true));
        inner.appendChild(front);
        if (flip) {
            const back = document.createElement('div');
            back.className = 'fly-face fly-back';
            inner.appendChild(back);
        }
        fly.appendChild(inner);
        document.body.appendChild(fly);

        target.style.visibility = 'hidden';
        const dx = (fromRect.left + fromRect.width / 2) - (endRect.left + endRect.width / 2);
        const dy = (fromRect.top + fromRect.height / 2) - (endRect.top + endRect.height / 2);

        const travel = fly.animate(
            [
                { transform: `translate(${dx}px, ${dy}px) scale(0.55)` },
                { transform: 'translate(0, 0) scale(1)' },
            ],
            { duration: 480, easing: 'cubic-bezier(0.2, 0.7, 0.3, 1)' }
        );
        if (flip) {
            inner.animate(
                [
                    { transform: 'rotateY(180deg)' },
                    { transform: 'rotateY(180deg)', offset: 0.35 },
                    { transform: 'rotateY(0deg)' },
                ],
                { duration: 480, easing: 'ease-in-out' }
            );
        }
        const finish = () => {
            target.style.visibility = '';
            fly.remove();
        };
        travel.addEventListener('finish', finish);
        travel.addEventListener('cancel', finish);
    }

    async function loadMatchupStats() {
        try {
            const data = await postJson('/api/matchup-stats', {});
            const rows = data.stats || [];
            if (!rows.length) {
                ui.matchupStats.innerHTML = '<div class="tiny">No finished games yet.</div>';
                return;
            }
            ui.matchupStats.innerHTML = rows.map((row) => {
                const decided = (row.deck_a_wins || 0) + (row.deck_b_wins || 0);
                const rate = decided ? `${Math.round((row.deck_a_wins / decided) * 100)}%` : '—';
                const draws = row.draws ? `, ${row.draws} draw${row.draws === 1 ? '' : 's'}` : '';
                return `
                    <div class="stats-row">
                        <div class="stats-matchup">${escapeHtml(row.deck_a)} vs ${escapeHtml(row.deck_b)}</div>
                        <div class="stats-numbers tiny">${row.deck_a_wins} – ${row.deck_b_wins}${draws} over ${row.games} game${row.games === 1 ? '' : 's'} (${rate} for ${escapeHtml(row.deck_a)})</div>
                    </div>
                `;
            }).join('');
        } catch (error) {
            ui.matchupStats.innerHTML = `<div class="tiny">Stats unavailable: ${escapeHtml(String(error))}</div>`;
        }
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
            let playedFromRect = null;
            if (action.kind === 'play_card' && action.card_id) {
                const handEl = ui.hand.querySelector(`.hand-card[data-card-id="${CSS.escape(action.card_id)}"]`);
                if (handEl) playedFromRect = handEl.getBoundingClientRect();
            }
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
            if (playedFromRect) animateCardPlay(action.card_id, playedFromRect);
        } catch (error) {
            ui.status.textContent = String(error);
        }
    }

    async function aiMove() {
        try {
            const c = cfg();
            const oppHandRect = ui.oppHand.getBoundingClientRect();
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
            if (data.action && data.action.kind === 'play_card' && data.action.card_id) {
                animateCardPlay(data.action.card_id, oppHandRect, { flip: true });
            }
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

    function openSheet(modal) {
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeSheet(modal) {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    function init() {
        ui.btnSettings.onclick = () => {
            openSheet(ui.settingsModal);
            loadMatchupStats();
        };
        ui.btnHistory.onclick = () => {
            openSheet(ui.historyModal);
        };
        ui.btnCloseSettings.onclick = () => {
            closeSheet(ui.settingsModal);
        };
        ui.btnCloseHistory.onclick = () => {
            closeSheet(ui.historyModal);
        };
        [ui.settingsModal, ui.historyModal].forEach((modal) => {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) closeSheet(modal);
            });
        });
        ui.btnNewGame.onclick = () => {
            closeSheet(ui.settingsModal);
            newGame();
        };
        ui.btnEndTurn.onclick = () => {
            onEndTurn();
        };
        window.addEventListener('resize', () => {
            layoutHand(ui);
        });
        ui.cardInspector.addEventListener('click', () => {
            closeInspector();
        });
        document.addEventListener('click', (event) => {
            if (!app.selectedCardId) return;
            if (event.target.closest('.hand-card') || event.target.closest('.lane')) return;
            clearTapSelection();
        });

        refresh().catch((error) => {
            ui.status.textContent = String(error);
        });
    }

    return {
        init,
    };
}
