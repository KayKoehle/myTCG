import { postJson } from './api.js';
import { createCardStackPopup } from './cardstack.js';
import { actionLabel, cardArtTag, cardDisplayName, effectLabel, escapeHtml, findCardById, humanLegalActions, laneLabel, typeLabel } from './helpers.js';
import { renderSnapshot, layoutHand, updateEndTurnButton } from './render.js';
import { buildConfig, createAppState } from './state.js';

export function createGameController(ui) {
    const app = createAppState();
    const cardStack = createCardStackPopup(ui);

    const cfg = () => buildConfig(ui, app);
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

    function rerender(snapshot) {
        renderSnapshot({
            snapshot,
            ui,
            app,
            config: cfg(),
            cardStack,
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
        runHistoryAnimations(snapshot);
        maybeAutoAdvance();
    }

    // Engine-internal side index of the human player (options such as
    // "card|location|side" and the stacks dict are in side order).
    function humanSideIndex() {
        const snap = app.snapshot;
        const playerIds = Object.keys((snap && snap.victory_points) || { 1: 0, 2: 0 });
        return Math.max(0, playerIds.indexOf(String(cfg().player_id)));
    }

    function laneIsFull(loc) {
        const snap = app.snapshot;
        if (!snap) return false;
        const location = (snap.locations || []).find((l) => Number(l.location_id) === Number(loc));
        if (!location) return false;
        const total = Object.values(location.stacks || {}).reduce((sum, cards) => sum + (cards ? cards.length : 0), 0);
        return total >= (Number(location.capacity) || 7);
    }

    // "This location is full": fade a notice in and out over the lane and
    // flash its capacity bar and the 7/7 counter at the same time.
    function flashLaneFull(loc) {
        const zone = ui.lanes.querySelector(`.lane-row.lane-drop[data-location-id="${loc}"]`);
        const lane = zone && zone.closest('.lane');
        if (!lane) return;
        const capacity = lane.querySelector('.lane-capacity');
        if (capacity) {
            capacity.classList.remove('capacity-flash');
            void capacity.offsetWidth; // restart the CSS animation
            capacity.classList.add('capacity-flash');
            setTimeout(() => capacity.classList.remove('capacity-flash'), 1900);
        }
        if (lane.querySelector('.lane-full-notice')) return;
        const notice = document.createElement('div');
        notice.className = 'lane-full-notice';
        notice.textContent = 'This location is full';
        lane.appendChild(notice);
        setTimeout(() => notice.remove(), 1900);
    }

    function isOpeningMulligan(snapshot) {
        return snapshot.phase === 'MULLIGAN'
            && snapshot.pending_choice
            && snapshot.pending_choice.choice_kind === 'opening_mulligan';
    }

    // Custom pointer-based dragging: HTML5 drag-and-drop does not work on
    // touch screens, so hand cards and movable board cards drag via pointer
    // events (mouse, touch, and pen) with a floating ghost element.
    // The drop target carries a side too: dropping on the opponent's row of a
    // lane targets their side (Odysseus can move cards across the line).
    function dropTargetAtPoint(clientX, clientY) {
        const hit = document.elementFromPoint(clientX, clientY);
        if (!hit) return null;
        const lane = hit.closest('.lane');
        if (!lane || !ui.lanes.contains(lane)) return null;
        const zone = lane.querySelector('.lane-row.lane-drop[data-location-id]');
        if (!zone) return null;
        const loc = Number(zone.dataset.locationId);
        const row = hit.closest('.lane-row');
        const onOppRow = Boolean(row && row.classList.contains('lane-row-opp'));
        const sideIdx = onOppRow ? 1 - humanSideIndex() : humanSideIndex();
        return { loc, sideIdx };
    }

    function highlightRow(loc, sideIdx = null) {
        ui.lanes.querySelectorAll('.lane-row.drop-target').forEach((el) => el.classList.remove('drop-target'));
        if (loc === null) return;
        const rowClass = sideIdx !== null && sideIdx !== humanSideIndex() ? 'lane-row-opp' : 'lane-drop';
        const zone = ui.lanes.querySelector(`.lane-row.${rowClass}[data-location-id="${loc}"]`);
        if (zone) zone.classList.add('drop-target');
    }

    function beginPointerDrag(event, cardEl, payload) {
        if (event.button !== undefined && event.button !== 0) return;
        const pointerId = event.pointerId;
        const startX = event.clientX;
        const startY = event.clientY;
        let dragging = false;
        let ghost = null;

        const legalTarget = (target) => (payload.type === 'play'
            ? app.legalPlaySet.has(`${payload.cardId}|${target.loc}`)
            : app.legalMoveChoiceSet.has(`${payload.cardId}|${target.loc}|${target.sideIdx}`));

        const cleanup = () => {
            cardEl.removeEventListener('pointermove', onMove);
            cardEl.removeEventListener('pointerup', onUp);
            cardEl.removeEventListener('pointercancel', onCancel);
            if (ghost) ghost.remove();
            cardEl.classList.remove('dragging');
            highlightRow(null);
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
            const target = dropTargetAtPoint(moveEvent.clientX, moveEvent.clientY);
            if (target && legalTarget(target)) {
                highlightRow(target.loc, payload.type === 'play' ? null : target.sideIdx);
            } else {
                highlightRow(null);
            }
            moveEvent.preventDefault();
        };

        const onUp = async (upEvent) => {
            const wasDragging = dragging;
            cleanup();
            if (!wasDragging) return; // Plain tap: the click handlers take it.
            app.dragEndedAt = Date.now();
            const target = dropTargetAtPoint(upEvent.clientX, upEvent.clientY);
            if (!target) return;
            if (!legalTarget(target)) {
                if (laneIsFull(target.loc)) {
                    flashLaneFull(target.loc);
                } else {
                    const verb = payload.type === 'play' ? 'play' : 'move';
                    ui.status.textContent = `Cannot ${verb} ${cardDisplayName(payload.cardId, app.cardNameById)} to ${laneLabel(target.loc)} right now.`;
                }
                return;
            }
            if (payload.type === 'play') {
                await doAction({ kind: 'play_card', card_id: payload.cardId, location_id: target.loc });
                return;
            }
            const pending = app.snapshot && app.snapshot.pending_choice;
            if (!pending) return;
            const option = (pending.options || []).find((opt) => {
                if (typeof opt !== 'string') return false;
                const parts = opt.split('|');
                return parts.length === 3
                    && parts[0] === payload.cardId
                    && Number(parts[1]) === target.loc
                    && Number(parts[2]) === target.sideIdx;
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
                    if (laneIsFull(loc)) {
                        flashLaneFull(loc);
                    } else {
                        ui.status.textContent = `Cannot play ${cardDisplayName(cardId, app.cardNameById)} to ${laneLabel(loc)} right now.`;
                    }
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

    // --- History-driven animations ---------------------------------------
    // New action_history entries since the last render drive the round-end
    // crown animation and the opponent mulligan animation.

    function runHistoryAnimations(snapshot) {
        const history = Array.isArray(snapshot.action_history) ? snapshot.action_history : [];
        if (app.animMatchId !== snapshot.match_id) {
            // First render of a match (or a reload): nothing to replay.
            app.animMatchId = snapshot.match_id;
            app.historySeen = history.length;
            return;
        }
        const fresh = history.slice(app.historySeen);
        app.historySeen = history.length;
        for (const entry of fresh) {
            const raw = String(entry || '');
            if (raw.startsWith('round_result:')) {
                animateRoundResult(raw);
            } else if (raw.startsWith('mulligan_keep:')) {
                const parts = raw.split(':');
                if (Number(parts[1]) === cfg().ai_player_id && Number(parts[2]) > 0) {
                    animateOpponentMulligan(Number(parts[2]));
                }
            }
        }
    }

    function roundCrownSvg(golden) {
        const fill = golden ? '#ffc84f' : 'rgba(200,214,226,0.45)';
        const highlight = golden ? '#fff4b3' : 'rgba(255,255,255,0.5)';
        const stroke = golden ? 'rgba(255,236,166,0.95)' : 'rgba(214,228,240,0.7)';
        const crack = golden ? '' : '<path d="M12 3.6 L11.2 8 L13 11.5 L12 16.4" fill="none" stroke="rgba(10,22,34,0.85)" stroke-width="0.9"/>';
        return `<svg viewBox="0 0 24 18" aria-hidden="true">
            <path d="M2 14 L4 6 L8 10 L12 3 L16 10 L20 6 L22 14 Z" fill="${fill}" stroke="${stroke}" stroke-width="1.2"/>
            <path d="M5 12 L8.5 9.2 L12 5.5 L15.5 9.2 L19 12" fill="none" stroke="${highlight}" stroke-width="1" opacity="0.8"/>
            <rect x="2" y="14" width="20" height="2.8" rx="1" fill="${fill}" stroke="${stroke}" stroke-width="1.1"/>
            ${crack}
        </svg>`;
    }

    // Round end: a crown pops up center screen and flies to the round
    // winner's score track; a draw shows a cracked grey crown that shakes.
    function animateRoundResult(entry) {
        if (!window.Element.prototype.animate) return;
        const parts = entry.split(':');
        const roundNo = parts[1] || '?';
        const winner = parts[2] || '';
        const isDraw = winner === 'DRAW';
        const youWon = !isDraw && Number(winner) === cfg().player_id;

        const overlay = document.createElement('div');
        overlay.className = `round-result-overlay ${isDraw ? 'draw' : (youWon ? 'win-you' : 'win-opp')}`;
        const crown = document.createElement('div');
        crown.className = 'round-crown';
        crown.innerHTML = roundCrownSvg(!isDraw);
        const text = document.createElement('div');
        text.className = 'round-result-text';
        text.textContent = isDraw
            ? `Round ${roundNo}: Draw — no crown`
            : (youWon ? `Round ${roundNo}: You win the crown!` : `Round ${roundNo}: Opponent wins the crown`);
        overlay.append(crown, text);
        document.body.appendChild(overlay);

        const cleanup = () => overlay.remove();
        overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, fill: 'forwards' });
        const pop = crown.animate(
            [
                { transform: 'scale(0.2)', opacity: 0 },
                { transform: 'scale(1.18)', opacity: 1, offset: 0.7 },
                { transform: 'scale(1)', opacity: 1 },
            ],
            { duration: 520, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1.2)', fill: 'forwards' }
        );

        pop.addEventListener('finish', () => {
            if (isDraw) {
                const shake = crown.animate(
                    [
                        { transform: 'scale(1) rotate(0deg)' },
                        { transform: 'scale(1) rotate(-9deg)', offset: 0.2 },
                        { transform: 'scale(1) rotate(8deg)', offset: 0.4 },
                        { transform: 'scale(1) rotate(-6deg)', offset: 0.6 },
                        { transform: 'scale(1) rotate(0deg)', offset: 0.8 },
                        { transform: 'scale(0.92) rotate(0deg)', opacity: 0.8 },
                    ],
                    { duration: 900, easing: 'ease-in-out', fill: 'forwards' }
                );
                shake.addEventListener('finish', () => {
                    const fade = overlay.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 320, delay: 250, fill: 'forwards' });
                    fade.addEventListener('finish', cleanup);
                });
                return;
            }
            const sides = ui.scorePanel.querySelectorAll('.score-side');
            const target = sides[youWon ? 0 : 1] || ui.scorePanel;
            const targetRect = target.getBoundingClientRect();
            const crownRect = crown.getBoundingClientRect();
            const dx = targetRect.left + targetRect.width / 2 - (crownRect.left + crownRect.width / 2);
            const dy = targetRect.top + targetRect.height / 2 - (crownRect.top + crownRect.height / 2);
            text.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 400, delay: 520, fill: 'forwards' });
            const fly = crown.animate(
                [
                    { transform: 'translate(0, 0) scale(1)', opacity: 1 },
                    { transform: `translate(${dx}px, ${dy}px) scale(0.16)`, opacity: 0.9 },
                ],
                { duration: 700, delay: 420, easing: 'cubic-bezier(0.5, 0, 0.3, 1)', fill: 'forwards' }
            );
            fly.addEventListener('finish', () => {
                target.animate(
                    [{ transform: 'scale(1)' }, { transform: 'scale(1.18)' }, { transform: 'scale(1)' }],
                    { duration: 360, easing: 'ease-out' }
                );
                const fade = overlay.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' });
                fade.addEventListener('finish', cleanup);
            });
        });
        // Safety net: never leave the overlay behind if an animation is cancelled.
        setTimeout(() => { if (overlay.isConnected) overlay.remove(); }, 4200);
    }

    // Opponent mulligan: the swapped cards get X'ed over their hand, fly back
    // to their deck, and replacements fly from the deck into their hand.
    function animateOpponentMulligan(count) {
        if (!window.Element.prototype.animate) return;
        const handRect = ui.oppHand.getBoundingClientRect();
        const deckPile = ui.oppDeckCount && ui.oppDeckCount.closest('.peek-pile');
        const deckEl = deckPile ? deckPile.querySelector('.deck-stack') : null;
        const deckRect = deckEl ? deckEl.getBoundingClientRect() : handRect;
        if (!handRect.width) return;
        const n = Math.max(1, Math.min(4, Number(count) || 1));
        const cardW = 28;
        const cardH = 40;
        const spawn = (leftPx, topPx, withX) => {
            const ghost = document.createElement('div');
            ghost.className = 'mull-ghost';
            ghost.style.left = `${leftPx}px`;
            ghost.style.top = `${topPx}px`;
            if (withX) {
                const xMark = document.createElement('div');
                xMark.className = 'mull-ghost-x';
                xMark.textContent = '✕';
                ghost.appendChild(xMark);
            }
            document.body.appendChild(ghost);
            return ghost;
        };
        const deckX = deckRect.left + deckRect.width / 2 - cardW / 2;
        const deckY = deckRect.top + deckRect.height / 2 - cardH / 2;
        for (let i = 0; i < n; i += 1) {
            const x = handRect.left + (handRect.width / (n + 1)) * (i + 1) - cardW / 2;
            const y = handRect.top + handRect.height / 2 - cardH / 2;
            const ghost = spawn(x, y, true);
            const anim = ghost.animate(
                [
                    { transform: 'translate(0, 0) scale(0.6)', opacity: 0 },
                    { transform: 'translate(0, 0) scale(1)', opacity: 1, offset: 0.22 },
                    { transform: 'translate(0, 0) scale(1)', opacity: 1, offset: 0.5 },
                    { transform: `translate(${deckX - x}px, ${deckY - y}px) scale(0.75)`, opacity: 0.9, offset: 0.88 },
                    { transform: `translate(${deckX - x}px, ${deckY - y}px) scale(0.6)`, opacity: 0 },
                ],
                { duration: 1400, delay: i * 130, easing: 'ease-in-out' }
            );
            const done = () => ghost.remove();
            anim.addEventListener('finish', done);
            anim.addEventListener('cancel', done);
        }
        setTimeout(() => {
            for (let i = 0; i < n; i += 1) {
                const endX = handRect.left + (handRect.width / (n + 1)) * (i + 1) - cardW / 2;
                const endY = handRect.top + handRect.height / 2 - cardH / 2;
                const ghost = spawn(deckX, deckY, false);
                const anim = ghost.animate(
                    [
                        { transform: 'translate(0, 0) scale(0.6)', opacity: 0 },
                        { transform: 'translate(0, 0) scale(1)', opacity: 1, offset: 0.3 },
                        { transform: `translate(${endX - deckX}px, ${endY - deckY}px) scale(1)`, opacity: 1, offset: 0.88 },
                        { transform: `translate(${endX - deckX}px, ${endY - deckY}px) scale(0.85)`, opacity: 0 },
                    ],
                    { duration: 1100, delay: i * 130, easing: 'ease-in-out' }
                );
                const done = () => ghost.remove();
                anim.addEventListener('finish', done);
                anim.addEventListener('cancel', done);
            }
        }, 1450 + n * 130);
    }

    // --- Opponent turn indicator -------------------------------------------
    // While the AI acts, the End Turn button flips over (like the cards do)
    // into a disabled, differently colored "Opponent's Turn" state.

    function setOpponentTurn(active) {
        if (app.opponentTurnActive === active) return;
        app.opponentTurnActive = active;
        if (active) app.opponentTurnStartedAt = Date.now();
        const btn = ui.btnEndTurn;
        if (!window.Element.prototype.animate) {
            updateEndTurnButton(ui, app, cfg());
            return;
        }
        const fold = btn.animate(
            [{ transform: 'rotateX(0deg)' }, { transform: 'rotateX(90deg)' }],
            { duration: 170, easing: 'ease-in' }
        );
        const applyAndUnfold = () => {
            updateEndTurnButton(ui, app, cfg());
            btn.animate(
                [{ transform: 'rotateX(-90deg)' }, { transform: 'rotateX(0deg)' }],
                { duration: 170, easing: 'ease-out' }
            );
        };
        fold.addEventListener('finish', applyAndUnfold);
        fold.addEventListener('cancel', applyAndUnfold);
    }

    // Even an opponent with nothing to play holds the turn for at least 2s so
    // their turn never resolves in an instant.
    async function finishOpponentTurn() {
        if (!app.opponentTurnActive) return;
        const snap = app.snapshot;
        const gameOver = snap && snap.phase === 'GAME_OVER';
        if (!gameOver) {
            const elapsed = Date.now() - app.opponentTurnStartedAt;
            if (elapsed < 2000) await sleep(2000 - elapsed);
        }
        setOpponentTurn(false);
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
        // Serialize submissions: a second click before the first response lands
        // (e.g. double-tapping a choice option) must not fire a stale action —
        // that could reach the server after the game is already over and 500.
        if (app.actionPending) return;
        if (app.snapshot && app.snapshot.phase === 'GAME_OVER') return;
        app.actionPending = true;
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
        } finally {
            app.actionPending = false;
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
                    // Pace the AI so its turn is watchable, not instant. Only
                    // its own turn is announced on the End Turn button;
                    // mid-turn AI choices (forced banishes etc.) get a short
                    // beat without hijacking the button.
                    const aiOwnsTurn = current === c.ai_player_id;
                    if (aiOwnsTurn) setOpponentTurn(true);
                    await sleep(aiOwnsTurn ? 700 : 350);
                    await aiMove();
                    continue;
                }
                await finishOpponentTurn();
                const drawAction = humanLegalActions(snap, c.player_id).find((a) => a.kind === 'draw_card');
                if (drawAction) {
                    await doAction(drawAction);
                    continue;
                }
                return;
            }
        } finally {
            await finishOpponentTurn();
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

        // Underworld piles open the shared card-stack popup (view mode) so their
        // contents can be browsed, reusing the selection popup's stack UI.
        [
            { el: ui.yourUnderworld, side: 'player', title: 'Your Underworld' },
            { el: ui.oppUnderworld, side: 'ai', title: 'Opponent Underworld' },
        ].forEach(({ el, side, title }) => {
            if (!el) return;
            el.style.cursor = 'pointer';
            el.addEventListener('click', () => {
                const snap = app.snapshot;
                if (!snap) return;
                const c = cfg();
                const playerId = String(side === 'player' ? c.player_id : c.ai_player_id);
                const cards = (snap.underworld && snap.underworld[playerId]) || [];
                if (!cards.length) return;
                cardStack.open({
                    mode: 'view',
                    title: `${title} (${cards.length})`,
                    cards: cards.map((card) => ({ card, option: card.id })),
                    onClose: () => {
                        // Restore the selection popup if the human still owes a choice.
                        const pending = app.snapshot && app.snapshot.pending_choice;
                        if (pending && Number(pending.player_id) === cfg().player_id) {
                            rerender(app.snapshot);
                        }
                    },
                });
            });
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
