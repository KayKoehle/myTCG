import { postJson, setLanHostBase, acquireLanHostLock, releaseLanHostLock } from './api.js';
import { anecdoteText, cardArtTag, cardDisplayName, cardPngUrl, effectLabel, escapeHtml, findCardById, humanLegalActions, laneLabel, stackPower, typeLabel } from './helpers.js';
import { eloDelta, placementsFromVp, sampleAiElo, streakMultiplier } from './elo.js';
import { activeEmotes, addCrowns, applyEloDelta, getElo, getWinStreak, recordCasualGame, recordGameResult } from './profile.js';
import {
    questOnCardBanished,
    questOnCardDrawn,
    questOnCardMoved,
    questOnCardPlayed,
    questOnCardRevived,
    questOnGameFinished,
    questOnMonsterDefeated,
    questOnPowerReached,
    questOnRoundWon,
} from './quests.js';
import { renderSnapshot, layoutHand, updateEndTurnButton } from './render.js';
import { buildConfig, createAppState } from './state.js';

export function createGameController(ui, cardStack) {
    const app = createAppState();
    let onExitToMenu = null;
    // Card ids the human actually played this match (for "win rate when played").
    let playedCardIds = new Set();

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
        bindOpponentChips(snapshot);
        bindLaneDots();
        layoutHand(ui);
        runHistoryAnimations(snapshot);
        // Deferred: a round-boundary crown can hand the round-starter role to
        // the player who's already active (they just ended the winning turn),
        // in which case this fires the new round's auto-draw. Calling it
        // synchronously here would nest inside the doAction() that produced
        // this snapshot — its actionPending guard is still set until that
        // call's own finally runs, so the auto-draw would be silently
        // dropped and the player stuck in DRAW with nothing to click.
        setTimeout(maybeAutoAdvance, 0);
        // Top-left slot: surrender flag while the match runs, home once it
        // ends — or before the player's mulligan, when leaving is still free.
        const gameOver = snapshot.phase === 'GAME_OVER';
        // A finished LAN match can't be rejoined — drop its saved session so the
        // menu never offers a dead game to reconnect to.
        if (gameOver && isLanGame()) clearLanSession();
        const showHome = gameOver || canQuitFree();
        if (ui.btnSurrender) {
            ui.btnSurrender.classList.toggle('hidden', showHome);
        }
        if (ui.btnHome) {
            ui.btnHome.classList.toggle('hidden', !showHome);
        }
        // Trading is a LAN-only affordance, and only while the match is live.
        if (ui.btnTrade) {
            ui.btnTrade.classList.toggle('hidden', !(isLanGame() && !gameOver));
        }
    }

    async function onSurrender() {
        closeSheet(ui.surrenderModal);
        await doAction({ kind: 'surrender', player_id: cfg().player_id });
    }

    // Seat order (player ids as strings, engine side order). Options such as
    // "card|location|side" and the stacks dict are keyed in this order.
    function seatOrder() {
        const snap = app.snapshot;
        if (snap && Array.isArray(snap.players) && snap.players.length) return snap.players;
        return Object.keys((snap && snap.victory_points) || { 1: 0, 2: 0 });
    }

    function humanSideIndex() {
        return Math.max(0, seatOrder().indexOf(String(cfg().player_id)));
    }

    function aiIds() {
        return (cfg().ai_player_ids || [cfg().ai_player_id]).map(Number);
    }

    // Local pass-and-play: every seat is a human sharing this device.
    function localSeats() {
        return (cfg().local_seat_ids || []).map(Number);
    }
    function isLocalGame() {
        return localSeats().length > 0;
    }
    function isLanGame() {
        return Boolean(app.lanGame);
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

    // Warnings and errors surface as a transient floating toast (the fixed
    // status element) so they never take layout space or shift the board.
    function flashStatus(message) {
        ui.status.textContent = String(message);
        clearTimeout(app.statusTimer);
        app.statusTimer = setTimeout(() => {
            ui.status.textContent = '';
        }, 2600);
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
        // Every row carries its engine side index (FFA lanes hold several
        // opponent rows); dropping outside a specific row targets your own.
        const row = hit.closest('.lane-row[data-side-idx]');
        const sideIdx = row ? Number(row.dataset.sideIdx) : humanSideIndex();
        return { loc, sideIdx };
    }

    function highlightRow(loc, sideIdx = null) {
        ui.lanes.querySelectorAll('.lane-row.drop-target').forEach((el) => el.classList.remove('drop-target'));
        if (loc === null) return;
        const side = sideIdx === null ? humanSideIndex() : sideIdx;
        const zone = ui.lanes.querySelector(`.lane-row[data-location-id="${loc}"][data-side-idx="${side}"]`);
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
                    flashStatus(`Cannot ${verb} ${cardDisplayName(payload.cardId, app.cardNameById)} to ${laneLabel(target.loc)} right now.`);
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
        if (!openingMulligan) return;

        // Tapping a card opens the inspector so it can be read before deciding
        // what to mulligan away — for both players' turns. The keep/redraw
        // choice lives on the card's own bottom button.
        ui.hand.querySelectorAll('.hand-card[data-card-id]').forEach((cardEl) => {
            cardEl.addEventListener('click', () => {
                openInspector(findCardById(snapshot, cardEl.dataset.cardId));
            });
        });

        const canActMulligan = Number(snapshot.pending_choice.player_id) === cfg().player_id;
        if (!canActMulligan) return;

        ui.hand.querySelectorAll('.hand-card[data-card-id]').forEach((cardEl) => {
            const toggleBtn = cardEl.querySelector('.mull-toggle');
            if (!toggleBtn) return;
            toggleBtn.addEventListener('click', (event) => {
                event.stopPropagation();
                const cardId = cardEl.dataset.cardId;
                if (!cardId) return;
                const redraw = !app.mulliganSelected.has(cardId);
                if (redraw) {
                    app.mulliganSelected.add(cardId);
                } else {
                    app.mulliganSelected.delete(cardId);
                }
                cardEl.classList.toggle('marked', redraw);
                toggleBtn.textContent = redraw ? 'Redraw' : 'Keep';
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
    }

    // Activate a card's "while on top" ability from the inspector (e.g. move
    // Enkidu to Gilgamesh). A single-target Enkidu move confirms itself.
    async function useCardAbility(cardId) {
        closeInspector();
        await doAction({ kind: 'use_ability', card_id: cardId });
        const pending = app.snapshot && app.snapshot.pending_choice;
        if (pending
            && Number(pending.player_id) === cfg().player_id
            && pending.choice_kind === 'enkidu_join_gilgamesh') {
            const target = (pending.options || []).find((opt) => opt !== 'PASS');
            if (target) await doAction({ kind: 'choose_option', option_id: target });
        }
    }

    function abilityButtonLabel(card) {
        if (card.name === 'Enkidu') return 'Move to Gilgamesh';
        return 'Use ability';
    }

    function openInspector(card) {
        if (!card || !card.name) return;
        ui.inspectorTitle.textContent = card.name;
        ui.inspectorType.textContent = typeLabel(card);
        ui.inspectorCost.textContent = card.cost ?? '?';
        ui.inspectorPower.textContent = card.power !== null && card.power !== undefined ? card.power : '?';
        ui.inspectorMedia.innerHTML = cardArtTag(card.name, 'inspector-art');
        ui.inspectorEffect.textContent = effectLabel(card);
        const anecdote = anecdoteText(card);
        ui.inspectorAnecdote.textContent = anecdote;
        ui.inspectorAnecdote.classList.toggle('hidden', !anecdote);
        const abilityAction = (card.id && app.snapshot)
            ? humanLegalActions(app.snapshot, cfg().player_id).find((a) => a.kind === 'use_ability' && a.card_id === card.id)
            : null;
        if (abilityAction) {
            ui.inspectorAction.textContent = abilityButtonLabel(card);
            ui.inspectorAction.classList.remove('hidden');
            ui.inspectorAction.onclick = (event) => {
                event.stopPropagation();
                useCardAbility(card.id);
            };
        } else {
            ui.inspectorAction.classList.add('hidden');
            ui.inspectorAction.onclick = null;
        }
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

        // Set-aside scenario chips (the Deluge) open the card in the inspector.
        ui.hud.querySelectorAll('.setaside-chip[data-setaside-pid]').forEach((chip) => {
            chip.addEventListener('click', (event) => {
                event.stopPropagation();
                const cards = (snapshot.set_aside || {})[chip.dataset.setasidePid] || [];
                openInspector(cards[Number(chip.dataset.setasideIdx) || 0]);
            });
        });

        // Compact FFA center rows: the "+N below" badge lists the whole stack.
        ui.lanes.querySelectorAll('.lane-row[data-player-id] .stack-buried-chip').forEach((chipEl) => {
            chipEl.addEventListener('click', (event) => {
                event.stopPropagation();
                const row = chipEl.closest('.lane-row[data-location-id][data-player-id]');
                if (!row) return;
                const loc = (snapshot.locations || []).find((l) => Number(l.location_id) === Number(row.dataset.locationId));
                const cards = loc ? (loc.stacks[row.dataset.playerId] || []) : [];
                if (!cards.length) return;
                cardStack.open({
                    mode: 'view',
                    title: 'Cards at this location',
                    cards: cards.slice().reverse().map((card) => ({ card, option: card.id })),
                    onClose: () => {
                        const pending = app.snapshot && app.snapshot.pending_choice;
                        if (pending && Number(pending.player_id) === cfg().player_id) {
                            rerender(app.snapshot);
                        }
                    },
                });
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
                        flashStatus(`Cannot play ${cardDisplayName(cardId, app.cardNameById)} to ${laneLabel(loc)} right now.`);
                    }
                    return;
                }
                clearTapSelection();
                await doAction({ kind: 'play_card', card_id: cardId, location_id: loc });
            });
        });
    }

    // FFA rival chips: tapping a chip opens that rival's underworld in the
    // shared card-stack popup (their hand stays hidden — the chip shows counts).
    function bindOpponentChips(snapshot) {
        if (!ui.oppChips) return;
        ui.oppChips.querySelectorAll('.opp-chip[data-player-id]').forEach((chip) => {
            chip.addEventListener('click', () => {
                const playerId = chip.dataset.playerId;
                const cards = (snapshot.underworld && snapshot.underworld[playerId]) || [];
                if (!cards.length) {
                    flashStatus(`${chip.dataset.playerName || 'Rival'}'s underworld is empty.`);
                    return;
                }
                cardStack.open({
                    mode: 'view',
                    title: `${chip.dataset.playerName || 'Rival'} — Underworld (${cards.length})`,
                    cards: cards.map((card) => ({ card, option: card.id })),
                    onClose: () => {
                        const pending = app.snapshot && app.snapshot.pending_choice;
                        if (pending && Number(pending.player_id) === cfg().player_id) {
                            rerender(app.snapshot);
                        }
                    },
                });
            });
        });
    }

    // FFA lane dots: tapping a dot scrolls the carousel to its lane.
    function bindLaneDots() {
        if (!ui.laneDots) return;
        ui.laneDots.querySelectorAll('.lane-dot[data-location-id]').forEach((dot) => {
            dot.addEventListener('click', () => {
                scrollLaneIntoView(Number(dot.dataset.locationId));
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

    // Card type lookups for typed quests ("Play X beings/heroes", ...).
    function knownCard(snapshot, cardId) {
        return (snapshot.known_cards && snapshot.known_cards[cardId]) || findCardById(snapshot, cardId);
    }

    function cardTypeCtx(card) {
        const type = String((card && card.type) || '').toLowerCase();
        const subtype = String((card && card.subtype) || '').toLowerCase();
        return {
            isBeing: type === 'being' || type === 'creature',
            isHero: subtype.includes('hero'),
        };
    }

    function runHistoryAnimations(snapshot) {
        const history = Array.isArray(snapshot.action_history) ? snapshot.action_history : [];
        const floodUsed = Boolean(snapshot.flood && snapshot.flood.used);
        if (app.animMatchId !== snapshot.match_id) {
            // First render of a match (or a reload): nothing to replay.
            app.animMatchId = snapshot.match_id;
            app.historySeen = history.length;
            app.floodSeen = floodUsed;
            // A brand-new match (empty history) with a set-aside scenario card
            // introduces it: big reveal, then it flies to its HUD chip.
            if (history.length === 0) animateSetAsideIntro(snapshot);
            return;
        }
        // The flood leaves no history entry of its own (only the banishes it
        // causes), so the wave pops when the scenario clock flips to "used".
        if (floodUsed && !app.floodSeen) {
            app.floodSeen = true;
            animateFloodEvent();
        }
        const fresh = history.slice(app.historySeen);
        app.historySeen = history.length;
        const you = cfg().player_id;
        // The fourth crown ends the game: the game-over animation replaces the
        // regular round-crown animation for that final round.
        const gameEnded = fresh.some((e) => String(e || '').startsWith('game_result:'));
        for (const entry of fresh) {
            const raw = String(entry || '');
            const parts = raw.split(':');
            if (raw.startsWith('round_result:')) {
                // Every crown won in a round is banked as shop currency. In
                // pass-and-play every seat plays on this device's profile, so
                // any seat's round win banks — not just whoever happens to be
                // holding the device when the entry lands.
                const roundWinner = Number(parts[2]);
                if (isLocalGame() ? localSeats().includes(roundWinner) : roundWinner === you) {
                    addCrowns(1);
                    if (app.statsMeta) questOnRoundWon();
                }
                if (!gameEnded) animateRoundResult(raw);
            } else if (raw.startsWith('play_card:')) {
                if (Number(parts[1]) === you && app.statsMeta) {
                    if (parts[2]) playedCardIds.add(parts[2]);
                    questOnCardPlayed(cardTypeCtx(knownCard(snapshot, parts[2])));
                }
            } else if (raw.startsWith('game_result:')) {
                recordFinishedGame(raw, snapshot);
                animateGameResult(raw);
            } else if (raw.startsWith('draw_card:')) {
                animateCardDraw(Number(parts[1]));
                if (Number(parts[1]) === you && app.statsMeta) questOnCardDrawn();
            } else if (raw.startsWith('banish:')) {
                // "Banish X enemy beings": a rival losing a being counts.
                const ctx = cardTypeCtx(knownCard(snapshot, parts[2]));
                if (Number(parts[1]) !== you && ctx.isBeing && app.statsMeta) questOnCardBanished();
            } else if (raw.startsWith('revive:')) {
                if (Number(parts[1]) === you && app.statsMeta) questOnCardRevived();
            } else if (raw.startsWith('move_card:')) {
                if (Number(parts[1]) === you && app.statsMeta) questOnCardMoved();
            } else if (raw.startsWith('monster_defeated:')) {
                if (Number(parts[1]) === you && app.statsMeta) questOnMonsterDefeated();
            } else if (raw.startsWith('mulligan_keep:')) {
                if (aiIds().includes(Number(parts[1])) && Number(parts[2]) > 0) {
                    animateOpponentMulligan(Number(parts[2]));
                }
            }
        }
        // "Reach X power on one location" checks the board after every change.
        if (fresh.length && app.statsMeta) {
            const humanPid = String(you);
            const best = Math.max(0, ...(snapshot.locations || []).map((loc) => (loc.side_power || {})[humanPid] ?? stackPower((loc.stacks || {})[humanPid] || [])));
            if (best > 0) questOnPowerReached(best);
        }
    }

    // Match intro for set-aside scenario cards (the Deluge): show the card
    // big in the center, then fly it onto its owner's HUD chip.
    function animateSetAsideIntro(snapshot) {
        if (!window.Element.prototype.animate) return;
        const setAside = snapshot.set_aside || {};
        const entries = [];
        for (const [pid, cards] of Object.entries(setAside)) {
            (cards || []).forEach((card, idx) => entries.push({ pid, idx, card }));
        }
        if (!entries.length) return;
        const threshold = (snapshot.flood && snapshot.flood.threshold) || 8;
        entries.forEach(({ pid, idx, card }, i) => {
            const isYou = Number(pid) === cfg().player_id;
            const overlay = document.createElement('div');
            overlay.className = 'setaside-intro';
            overlay.innerHTML = `
                <img class="setaside-intro-art" src="${cardPngUrl(card.name)}" alt="${escapeHtml(card.name)}"
                    draggable="false" onerror="this.style.display='none';">
                <div class="setaside-intro-title">${escapeHtml(card.name)}</div>
                <div class="setaside-intro-sub">${isYou ? 'Your scenario card' : "Your opponent's scenario card"} — set aside.
                    It floods the world once ${threshold} humans are in play.</div>
            `;
            document.body.appendChild(overlay);
            overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, delay: i * 400, fill: 'both' });

            const flyToChip = () => {
                const chip = ui.hud.querySelector(`.setaside-chip[data-setaside-pid="${CSS.escape(String(pid))}"][data-setaside-idx="${idx}"]`);
                const art = overlay.querySelector('.setaside-intro-art');
                if (!chip || !art) {
                    overlay.remove();
                    return;
                }
                const from = art.getBoundingClientRect();
                const to = chip.getBoundingClientRect();
                overlay.querySelectorAll('.setaside-intro-title, .setaside-intro-sub').forEach((el) => {
                    el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: 'forwards' });
                });
                overlay.animate([{ background: 'rgba(2, 8, 14, 0.82)' }, { background: 'rgba(2, 8, 14, 0)' }], { duration: 620, fill: 'forwards' });
                const dx = to.left + to.width / 2 - (from.left + from.width / 2);
                const dy = to.top + to.height / 2 - (from.top + from.height / 2);
                const scale = Math.max(0.08, to.height / Math.max(1, from.height));
                const fly = art.animate(
                    [
                        { transform: 'translate(0, 0) scale(1)', opacity: 1 },
                        { transform: `translate(${dx}px, ${dy}px) scale(${scale})`, opacity: 0.85 },
                    ],
                    { duration: 640, easing: 'cubic-bezier(0.5, 0, 0.3, 1)', fill: 'forwards' }
                );
                const done = () => {
                    chip.animate(
                        [{ transform: 'scale(1)' }, { transform: 'scale(1.15)' }, { transform: 'scale(1)' }],
                        { duration: 320, easing: 'ease-out' }
                    );
                    overlay.remove();
                };
                fly.addEventListener('finish', done);
                fly.addEventListener('cancel', done);
            };
            setTimeout(flyToChip, 1600 + i * 400);
            // Safety net if animations get cancelled midway.
            setTimeout(() => { if (overlay.isConnected) overlay.remove(); }, 4500 + i * 400);
        });
    }

    // The Deluge resolves: a wave washes over the whole board, then recedes,
    // while the banished humans disappear underneath it.
    function animateFloodEvent() {
        if (!window.Element.prototype.animate) return;
        if (document.querySelector('.flood-overlay')) return;
        const overlay = document.createElement('div');
        overlay.className = 'flood-overlay';
        overlay.innerHTML = `
            <div class="flood-water flood-water-back"></div>
            <div class="flood-water"></div>
            <div class="flood-emoji">🌊</div>
            <div class="flood-title">The Flood!</div>
            <div class="flood-sub">The Great Deluge washes every unprotected human into the underworld.</div>
        `;
        document.body.appendChild(overlay);
        overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 360, fill: 'both' });
        overlay.querySelectorAll('.flood-water').forEach((water, i) => {
            water.animate(
                [
                    { transform: 'translateY(115%)' },
                    { transform: 'translateY(0%)', offset: 0.3 },
                    { transform: 'translateY(7%)', offset: 0.5 },
                    { transform: 'translateY(0%)', offset: 0.72 },
                    { transform: 'translateY(118%)' },
                ],
                { duration: 3100, delay: i * 150, easing: 'ease-in-out', fill: 'both' }
            );
        });
        const emoji = overlay.querySelector('.flood-emoji');
        emoji.animate(
            [
                { transform: 'translateY(46px) scale(0.5)', opacity: 0 },
                { transform: 'translateY(0) scale(1.18)', opacity: 1, offset: 0.35 },
                { transform: 'translateY(-6px) scale(1)', opacity: 1 },
            ],
            { duration: 950, delay: 250, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1.1)', fill: 'both' }
        );
        const fade = overlay.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 520, delay: 2800, fill: 'forwards' });
        const done = () => overlay.remove();
        fade.addEventListener('finish', done);
        fade.addEventListener('cancel', done);
        // Safety net if animations get cancelled midway.
        setTimeout(() => { if (overlay.isConnected) overlay.remove(); }, 4200);
    }

    // A finished game (win, loss, draw, surrender — they all append a
    // game_result entry exactly once) feeds the win-rate statistics and the
    // quest system. Only menu-started matches carry statsMeta; the settings
    // sheet's debug games don't count.
    function recordFinishedGame(entry, snapshot) {
        if (!app.statsMeta) {
            // Local hotseat and LAN matches carry no statsMeta (no single rated
            // deck), but they are still finished games: count them so the Decks
            // and Quests unlocks progress the same way solo games do.
            if (isLocalGame() || isLanGame()) recordCasualGame();
            return;
        }
        const winner = entry.split(':')[1] || '';
        const won = winner !== 'DRAW' && Number(winner) === cfg().player_id;
        const vp = snapshot.victory_points || {};
        const flawless = won && aiIds().every((id) => Number(vp[String(id)] || 0) === 0);
        recordGameResult({
            deckId: app.statsMeta.deckId,
            cardIds: app.statsMeta.cardIds,
            playedCardIds: Array.from(playedCardIds),
            won,
            mode: app.statsMeta.mode || null,
        });
        // recordGameResult above already counted this game, so getWinStreak()
        // is the up-to-date streak the win-streak quests track.
        questOnGameFinished({ won, flawless, streak: getWinStreak(), deckId: app.statsMeta.deckId });

        // One rating across all modes: score the game pairwise against every
        // rated AI rival by final placement (VP order handles draws and
        // surrenders alike) and move the player's Elo once.
        const ranks = placementsFromVp(vp);
        const rivals = aiIds().map((id) => ({
            playerId: id,
            elo: (app.aiElos || {})[id] ?? app.playerElo ?? getElo(),
        }));
        let delta = eloDelta(getElo(), rivals, ranks[cfg().player_id], ranks);
        // Streak heat: recordGameResult above already counted this game, so
        // getWinStreak() includes it — the bonus kicks in from the 2nd
        // straight win and only ever grows a gain.
        if (won && delta > 0) delta = Math.round(delta * streakMultiplier(getWinStreak()));
        app.lastEloDelta = delta;
        applyEloDelta(delta);
    }

    // A card back flies from the drawing player's deck pile into their hand.
    function animateCardDraw(playerId) {
        if (!window.Element.prototype.animate) return;
        const isHuman = Number(playerId) === cfg().player_id;
        const deckPile = (isHuman ? ui.yourDeckCount : ui.oppDeckCount).closest('.peek-pile');
        const deckEl = deckPile ? deckPile.querySelector('.deck-stack') : null;
        const handEl = isHuman ? ui.hand : ui.oppHand;
        if (!deckEl || !handEl) return;
        const from = deckEl.getBoundingClientRect();
        const to = handEl.getBoundingClientRect();
        if (!from.width || !to.width) return;

        const w = isHuman ? 56 : 30;
        const h = isHuman ? 80 : 42;
        const ghost = document.createElement('div');
        ghost.className = isHuman ? 'draw-ghost' : 'draw-ghost draw-ghost-opp';
        ghost.style.width = `${w}px`;
        ghost.style.height = `${h}px`;
        const startX = from.left + from.width / 2 - w / 2;
        const startY = from.top + from.height / 2 - h / 2;
        // Aim at the newest card's spot: the right end of the hand row.
        const endX = to.left + to.width * 0.78 - w / 2;
        const endY = to.top + to.height / 2 - h / 2;
        ghost.style.left = `${startX}px`;
        ghost.style.top = `${startY}px`;
        document.body.appendChild(ghost);
        const anim = ghost.animate(
            [
                { transform: 'translate(0, 0) scale(0.7) rotate(-6deg)', opacity: 0 },
                { transform: 'translate(0, 0) scale(1) rotate(-6deg)', opacity: 1, offset: 0.25 },
                { transform: `translate(${endX - startX}px, ${endY - startY}px) scale(1.05) rotate(0deg)`, opacity: 1, offset: 0.85 },
                { transform: `translate(${endX - startX}px, ${endY - startY}px) scale(0.9) rotate(0deg)`, opacity: 0 },
            ],
            { duration: 620, easing: 'cubic-bezier(0.3, 0.7, 0.3, 1)' }
        );
        const done = () => ghost.remove();
        anim.addEventListener('finish', done);
        anim.addEventListener('cancel', done);
    }

    // Game over: a full-screen animation distinct from the round-crown one,
    // and different for victory and defeat.
    function animateGameResult(entry) {
        if (!window.Element.prototype.animate) return;
        const winner = entry.split(':')[1] || '';
        const isDraw = winner === 'DRAW';
        const youWon = !isDraw && Number(winner) === cfg().player_id;
        // Pass-and-play shares one screen among several humans, so a "You won /
        // You lost" frame reads as if everyone claimed the crown. Name the
        // winning seat instead and celebrate only them; solo and LAN games keep
        // the per-viewer framing (each player has their own device there).
        const hotseat = isLocalGame();
        const celebrate = !isDraw && (hotseat || youWon);
        const winnerLabel = isDraw ? '' : seatLabel(winner);

        const overlay = document.createElement('div');
        overlay.className = `game-result-overlay ${isDraw ? 'game-draw' : (celebrate ? 'game-win' : 'game-loss')}`;
        const crowns = document.createElement('div');
        crowns.className = 'game-result-crowns';
        const crownCount = isDraw ? 1 : 4;
        for (let i = 0; i < crownCount; i += 1) {
            const crown = document.createElement('div');
            crown.className = 'game-result-crown';
            crown.innerHTML = roundCrownSvg(celebrate);
            crowns.appendChild(crown);
        }
        const title = document.createElement('div');
        title.className = 'game-result-title';
        title.textContent = isDraw ? 'Draw' : (hotseat ? `${winnerLabel} wins!` : (youWon ? 'Victory!' : 'Defeat'));
        const sub = document.createElement('div');
        sub.className = 'game-result-sub';
        sub.textContent = isDraw
            ? 'The game ends with no winner'
            : (hotseat
                ? `${winnerLabel} claimed the fourth crown`
                : (youWon ? 'You claimed the fourth crown' : 'Your opponent claimed the fourth crown'));
        overlay.append(crowns, title, sub);
        // Rated (menu-started) games show how the result moved the rating.
        let eloLine = null;
        if (app.statsMeta && app.lastEloDelta !== null) {
            const delta = app.lastEloDelta;
            eloLine = document.createElement('div');
            eloLine.className = `game-result-elo ${delta >= 0 ? 'elo-up' : 'elo-down'}`;
            const streak = getWinStreak();
            const streakNote = youWon && streak >= 2 ? ` · 🔥 ${streak}-win streak` : '';
            eloLine.textContent = `${delta >= 0 ? '+' : ''}${delta} Elo → ${getElo()}${streakNote}`;
            overlay.appendChild(eloLine);
        }
        document.body.appendChild(overlay);

        overlay.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, fill: 'forwards' });
        const crownEls = Array.from(crowns.children);
        crownEls.forEach((crownEl, i) => {
            if (celebrate) {
                crownEl.animate(
                    [
                        { transform: 'translateY(40px) scale(0.2)', opacity: 0 },
                        { transform: 'translateY(-10px) scale(1.25)', opacity: 1, offset: 0.65 },
                        { transform: 'translateY(0) scale(1)', opacity: 1 },
                    ],
                    { duration: 560, delay: 220 + i * 160, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1.25)', fill: 'both' }
                );
            } else {
                crownEl.animate(
                    [
                        { transform: 'translateY(-60px) rotate(0deg)', opacity: 0 },
                        { transform: 'translateY(6px) rotate(-8deg)', opacity: 1, offset: 0.55 },
                        { transform: 'translateY(0) rotate(4deg)', opacity: 0.85, offset: 0.8 },
                        { transform: 'translateY(2px) rotate(0deg)', opacity: 0.8 },
                    ],
                    { duration: 700, delay: 200 + i * 120, easing: 'ease-in', fill: 'both' }
                );
            }
        });
        title.animate(
            [
                { transform: 'scale(0.6)', opacity: 0 },
                { transform: 'scale(1.06)', opacity: 1, offset: 0.7 },
                { transform: 'scale(1)', opacity: 1 },
            ],
            { duration: 520, delay: 480, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1.15)', fill: 'both' }
        );
        sub.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 400, delay: 900, fill: 'both' });
        if (eloLine) {
            eloLine.animate(
                [
                    { transform: 'translateY(8px)', opacity: 0 },
                    { transform: 'translateY(0)', opacity: 1 },
                ],
                { duration: 400, delay: 1150, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1.1)', fill: 'both' }
            );
        }

        const fade = overlay.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 500, delay: 3600, fill: 'forwards' });
        const cleanup = () => overlay.remove();
        fade.addEventListener('finish', cleanup);
        fade.addEventListener('cancel', cleanup);
        setTimeout(() => { if (overlay.isConnected) overlay.remove(); }, 5200);
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
        // Shared-screen hotseat: name the seat that took the crown rather than
        // framing it as the current holder's win/loss.
        const hotseat = isLocalGame();

        const overlay = document.createElement('div');
        overlay.className = `round-result-overlay ${isDraw ? 'draw' : ((youWon || hotseat) ? 'win-you' : 'win-opp')}`;
        const crown = document.createElement('div');
        crown.className = 'round-crown';
        crown.innerHTML = roundCrownSvg(!isDraw);
        const text = document.createElement('div');
        text.className = 'round-result-text';
        text.textContent = isDraw
            ? `Round ${roundNo}: Draw — no crown`
            : (hotseat
                ? `Round ${roundNo}: ${seatLabel(winner)} wins the crown!`
                : (youWon ? `Round ${roundNo}: You win the crown!` : `Round ${roundNo}: Opponent wins the crown`));
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
            const byPlayer = winner && ui.scorePanel.querySelector(`.score-side[data-player-id="${winner}"]`);
            const sides = ui.scorePanel.querySelectorAll('.score-side');
            const target = byPlayer || sides[youWon ? 0 : 1] || ui.scorePanel;
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

    // --- Emotes --------------------------------------------------------------
    // A speech bubble pops up above the emote button, floats up, and fades —
    // purely cosmetic flavor, there is no human opponent to receive it.

    function sendEmote(text) {
        const anchor = ui.btnEmote.getBoundingClientRect();
        const bubble = document.createElement('div');
        bubble.className = 'emote-bubble';
        bubble.textContent = text;
        bubble.style.left = `${anchor.left + anchor.width / 2}px`;
        bubble.style.top = `${anchor.top - 10}px`;
        document.body.appendChild(bubble);
        if (!window.Element.prototype.animate) {
            setTimeout(() => bubble.remove(), 2400);
            return;
        }
        const anim = bubble.animate(
            [
                { transform: 'translate(-50%, 10px) scale(0.6)', opacity: 0 },
                { transform: 'translate(-50%, -10px) scale(1.05)', opacity: 1, offset: 0.18 },
                { transform: 'translate(-50%, -16px) scale(1)', opacity: 1, offset: 0.78 },
                { transform: 'translate(-50%, -46px) scale(0.96)', opacity: 0 },
            ],
            { duration: 2400, easing: 'ease-out' }
        );
        const done = () => bubble.remove();
        anim.addEventListener('finish', done);
        anim.addEventListener('cancel', done);
    }

    function closeEmoteMenu() {
        ui.emoteMenu.classList.remove('open');
        ui.emoteMenu.setAttribute('aria-hidden', 'true');
    }

    function initEmotes() {
        if (!ui.btnEmote || !ui.emoteMenu) return;
        // Rebuilt on every open so emotes bought in the shop show up
        // immediately in the next game, honoring the playing deck's loadout.
        const renderEmoteMenu = () => {
            const emotes = activeEmotes();
            ui.emoteMenu.innerHTML = emotes.map((emote) => (
                `<button type="button" class="emote-option" data-emote-id="${emote.id}">${escapeHtml(emote.text)}</button>`
            )).join('');
            ui.emoteMenu.querySelectorAll('.emote-option').forEach((btn) => {
                btn.addEventListener('click', (event) => {
                    event.stopPropagation();
                    const emote = emotes.find((e) => e.id === btn.dataset.emoteId);
                    closeEmoteMenu();
                    if (emote) sendEmote(emote.text);
                });
            });
        };
        ui.btnEmote.onclick = (event) => {
            event.stopPropagation();
            const open = ui.emoteMenu.classList.toggle('open');
            if (open) renderEmoteMenu();
            ui.emoteMenu.setAttribute('aria-hidden', open ? 'false' : 'true');
        };
        document.addEventListener('click', (event) => {
            if (!ui.emoteMenu.classList.contains('open')) return;
            if (event.target.closest('.emote-widget')) return;
            closeEmoteMenu();
        });
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
            deck_a_cards: c.deck_a_cards,
            decks: c.decks,
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
                deck_a_cards: c.deck_a_cards,
                decks: c.decks,
            });
            rerender(data.snapshot);
            if (playedFromRect) animateCardPlay(action.card_id, playedFromRect);
        } catch (error) {
            if (isLanGame()) {
                // Tell a lost connection apart from a rules rejection: if we can
                // still read the host's state, the move was simply rejected (or
                // already applied) — show it and re-sync. If we can't, we've
                // dropped, so reconnect instead of silently losing the match.
                try {
                    await refresh();
                    flashStatus(error);
                } catch (offline) {
                    startReconnect();
                }
            } else {
                flashStatus(error);
            }
        } finally {
            app.actionPending = false;
        }
    }

    async function aiMove(aiPlayerId = null) {
        try {
            const c = cfg();
            const actorId = aiPlayerId ?? c.ai_player_id;
            // Where the AI's play animation starts: the 2P opponent hand, or
            // that rival's chip in FFA.
            const chipEl = ui.oppChips && ui.oppChips.querySelector(`.opp-chip[data-player-id="${actorId}"]`);
            const originEl = (chipEl && chipEl.offsetParent) ? chipEl : ui.oppHand;
            const oppHandRect = originEl.getBoundingClientRect();
            const data = await postJson('/api/ai-move', {
                match_id: c.match_id,
                ai_player_id: actorId,
                viewer_player_id: c.viewer_player_id,
                seed: c.seed,
                deck_a: c.deck_a,
                deck_b: c.deck_b,
                deck_a_cards: c.deck_a_cards,
                decks: c.decks,
                checkpoint_path: c.checkpoint_path,
                device: c.device,
                ai_elo: (c.ai_elos || {})[actorId],
            });
            rerender(data.snapshot);
            if (data.action && data.action.kind === 'play_card' && data.action.card_id) {
                scrollLaneIntoView(data.action.location_id);
                animateCardPlay(data.action.card_id, oppHandRect, { flip: true });
            }
        } catch (error) {
            flashStatus(error);
        }
    }

    // FFA lane carousel: bring the lane being acted on into view.
    function scrollLaneIntoView(locationId) {
        if (locationId === null || locationId === undefined) return;
        if (!ui.lanes.classList.contains('lanes-carousel')) return;
        const zone = ui.lanes.querySelector(`.lane-row.lane-drop[data-location-id="${locationId}"], .lane[data-location-id="${locationId}"]`);
        const lane = zone && (zone.closest('.lane') || zone);
        if (lane && lane.scrollIntoView) {
            lane.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
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

    // Which seat is being asked to act right now: the pending chooser if there
    // is one, otherwise whoever's turn it is.
    function actorSeat(snap) {
        return snap.pending_choice ? Number(snap.pending_choice.player_id) : Number(snap.current_player_id);
    }

    // Local pass-and-play has no AI to auto-advance; instead, when the seat that
    // must act is a *different* human sharing the device, we cover the board and
    // ask players to physically hand it over before revealing the new hand.
    async function maybePassAndPlay() {
        const snap = app.snapshot;
        if (!snap || snap.phase === 'GAME_OVER') return;
        const you = cfg().player_id;
        const actor = actorSeat(snap);
        if (actor !== you && localSeats().includes(actor) && !app.passPending) {
            await promptPassAndPlay(actor);
            return;
        }
        // Start of your own turn: auto-draw, mirroring the AI-game path so the
        // active player doesn't get stuck in the DRAW phase with no affordance.
        if (actor === you && !snap.pending_choice) {
            const drawAction = humanLegalActions(snap, you).find((a) => a.kind === 'draw_card');
            if (drawAction) await doAction(drawAction);
        }
    }

    function seatLabel(seatId) {
        const order = seatOrder();
        const idx = order.indexOf(String(seatId));
        return `Player ${idx >= 0 ? idx + 1 : seatId}`;
    }

    // Full-screen opaque hand-off: hides the outgoing player's board until the
    // incoming player taps, then swaps the active seat and re-renders their view.
    function promptPassAndPlay(seatId) {
        return new Promise((resolve) => {
            app.passPending = true;
            const overlay = document.createElement('div');
            overlay.className = 'passplay-overlay';
            overlay.innerHTML = `
                <div class="passplay-card">
                    <div class="passplay-icon" aria-hidden="true">🔄</div>
                    <div class="passplay-title">Pass the device</div>
                    <div class="passplay-sub">Hand it to <strong>${escapeHtml(seatLabel(seatId))}</strong>.</div>
                    <button class="passplay-btn" type="button">I'm ${escapeHtml(seatLabel(seatId))} — ready</button>
                </div>`;
            const finish = async () => {
                overlay.remove();
                app.passPending = false;
                app.activeSeatId = Number(seatId);
                await refresh();
                resolve();
            };
            overlay.querySelector('.passplay-btn').addEventListener('click', finish);
            document.body.appendChild(overlay);
        });
    }

    // LAN: no AI and no hand-off. When it is a remote player's turn we poll the
    // host for their moves; on our own turn we stop polling and (like every
    // mode) auto-draw so play flows straight into the main phase.
    function clearLanPoll() {
        if (app.lanPollTimer) { clearTimeout(app.lanPollTimer); app.lanPollTimer = null; }
    }
    function scheduleLanPoll() {
        if (app.lanPollTimer || app.lanReconnecting) return;
        app.lanPollTimer = setTimeout(async () => {
            app.lanPollTimer = null;
            if (!isLanGame() || app.lanReconnecting) return;
            try {
                await refresh();
                app.lanPollFails = 0;
            } catch (error) {
                // One dropped poll is usually a blip; a second in a row means the
                // host is really unreachable, so escalate to the reconnect flow.
                app.lanPollFails = (app.lanPollFails || 0) + 1;
                if (app.lanPollFails >= 2) startReconnect();
                else scheduleLanPoll();
            }
        }, 1500);
    }
    async function lanTick() {
        const snap = app.snapshot;
        if (!snap || snap.phase === 'GAME_OVER') { clearLanPoll(); return; }
        const you = cfg().player_id;
        if (actorSeat(snap) === you) {
            clearLanPoll();
            if (!snap.pending_choice) {
                const drawAction = humanLegalActions(snap, you).find((a) => a.kind === 'draw_card');
                if (drawAction) await doAction(drawAction);
            }
            return;
        }
        scheduleLanPoll();
    }

    async function maybeAutoAdvance() {
        // Local games are driven entirely by the humans present; never call the
        // AI, just orchestrate the pass-and-play hand-off.
        if (isLanGame()) {
            await lanTick();
            return;
        }
        if (isLocalGame()) {
            await maybePassAndPlay();
            return;
        }
        if (app.autoRunning) return;
        app.autoRunning = true;
        try {
            // Enough iterations for several full FFA rounds (up to 4 AI seats
            // each playing multiple actions per turn).
            for (let i = 0; i < 160; i += 1) {
                const snap = app.snapshot;
                if (!snap || snap.phase === 'GAME_OVER') return;
                const c = cfg();
                const rivals = aiIds();
                const current = Number(snap.current_player_id);
                // A pending choice for the human blocks everything — even when
                // the turn has already passed to an AI (e.g. a top ability
                // offered at end of turn). Calling the AI here would only 500.
                if (snap.pending_choice && Number(snap.pending_choice.player_id) === c.player_id) return;
                const pendingAiId = snap.pending_choice && rivals.includes(Number(snap.pending_choice.player_id))
                    ? Number(snap.pending_choice.player_id)
                    : null;
                if (rivals.includes(current) || pendingAiId !== null) {
                    // Pace the AI so its turn is watchable, not instant. Only
                    // a rival's own turn is announced on the End Turn button;
                    // mid-turn AI choices (forced banishes etc.) get a short
                    // beat without hijacking the button.
                    const aiOwnsTurn = rivals.includes(current);
                    if (aiOwnsTurn) setOpponentTurn(true);
                    await sleep(aiOwnsTurn ? 700 : 350);
                    await aiMove(pendingAiId !== null ? pendingAiId : current);
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

    // When ending the turn would waste a move (a card still playable, a hero
    // ability still up, or unspent mana on the table), return a short reason to
    // confirm; otherwise null and the turn ends straight away. Guards misclicks
    // without nagging when there is genuinely nothing left to do.
    function endTurnMisclickReason(snap) {
        const legal = humanLegalActions(snap, cfg().player_id);
        const plays = legal.filter((a) => a.kind === 'play_card' || a.kind === 'use_ability');
        if (plays.length === 0) return null;
        const human = String(cfg().player_id);
        const manaLeft = Number((snap.mana_pool || {})[human]) || 0;
        const playableCards = new Set(plays.filter((a) => a.card_id != null).map((a) => a.card_id)).size;
        let msg = playableCards > 1
            ? `You still have ${playableCards} cards you can play`
            : 'You still have a move you can make';
        if (manaLeft > 0) msg += ` and ${manaLeft} unspent mana`;
        return `${msg}. End your turn anyway?`;
    }

    async function endTurnNow(snap) {
        const action = humanLegalActions(snap, cfg().player_id).find((a) => a.kind === 'end_turn');
        if (action) {
            await doAction(action);
        }
    }

    async function onEndTurn() {
        const snap = app.snapshot;
        if (!snap) return;

        if (snap.phase === 'GAME_OVER') {
            // Rematch: same decks, fresh match. The header home button is the
            // way back to the main menu.
            await newGame();
            return;
        }

        if (isOpeningMulligan(snap) && Number(snap.pending_choice.player_id) === cfg().player_id) {
            await confirmMulligan();
            return;
        }

        const reason = endTurnMisclickReason(snap);
        if (reason) {
            ui.endTurnCopy.textContent = reason;
            openSheet(ui.endTurnModal);
            return;
        }
        await endTurnNow(snap);
    }

    async function newGame() {
        app.matchId = `snap-match-${Math.floor(Math.random() * 1_000_000)}`;
        app.seed = Math.floor(Math.random() * 1_000_000_000);
        app.mulliganSelected.clear();
        app.opponentTurnActive = false;
        app.passPending = false;
        // Rematch starts back at the first local seat holding the device.
        if (app.localSeatIds && app.localSeatIds.length) app.activeSeatId = Number(app.localSeatIds[0]);
        // Every match (menu, rematch, debug) is against rated rivals drawn
        // near the player's current Elo; the server plays them at exactly
        // that strength.
        app.playerElo = getElo();
        app.aiElos = {};
        for (const id of aiIds()) app.aiElos[id] = sampleAiElo(app.playerElo);
        app.lastEloDelta = null;
        await refresh();
    }

    // Entry point for the main menu's Play button: the player's (possibly
    // edited) deck against the given AI deck(s), in a fresh match. statsMeta
    // identifies the profile deck so the result can be recorded. `decks`
    // (one deck name per seat, human first) switches the match to FFA.
    async function startGame({ deckAName, deckACards, deckBName, decks = null, statsMeta = null, localSeatIds = null }) {
        // Starting any non-LAN match tears down lingering LAN state first —
        // releases the host Wi-Fi lock, clears the rejoin session, stops polling.
        endLanGame();
        app.deckAName = deckAName || null;
        app.deckACards = deckACards || null;
        app.deckBName = deckBName || null;
        app.deckNames = Array.isArray(decks) && decks.length > 2 ? decks : null;
        // Local pass-and-play: all seats are human, no AI to drive. Otherwise
        // seats 2..n are AI rivals.
        app.localSeatIds = Array.isArray(localSeatIds) && localSeatIds.length ? localSeatIds.map(Number) : null;
        app.activeSeatId = app.localSeatIds ? Number(app.localSeatIds[0]) : app.humanPlayerId;
        app.aiPlayerIds = app.localSeatIds
            ? []
            : (app.deckNames
                ? Array.from({ length: app.deckNames.length - 1 }, (_, i) => i + 2)
                : [app.aiPlayerId]);
        app.statsMeta = statsMeta;
        playedCardIds = new Set();
        try {
            await newGame();
        } catch (error) {
            flashStatus(error);
        }
    }

    // Enter a LAN match already created on the host. `hostBase` is null when we
    // are the host (same-origin), or the host's URL when we are a guest. Our
    // seat is `playerId`; every other seat is a remote human.
    async function startLanGame({ hostBase = null, matchId, seed, playerId, decks = null }) {
        endLanGame();
        app.lanGame = true;
        app.lanHostBase = hostBase || null;
        setLanHostBase(hostBase || null);
        app.localSeatIds = null;
        app.aiPlayerIds = [];
        app.humanPlayerId = Number(playerId);
        app.matchId = matchId;
        app.seed = Number(seed);
        app.deckNames = Array.isArray(decks) && decks.length > 2 ? decks : null;
        app.deckAName = decks ? decks[0] : null;
        app.deckBName = decks && decks[1] ? decks[1] : null;
        app.deckACards = null;
        app.statsMeta = null;
        app.mulliganSelected.clear();
        app.opponentTurnActive = false;
        app.passPending = false;
        playedCardIds = new Set();
        // Hosting: keep the Wi-Fi radio awake so guests can reach us if the
        // screen sleeps. Guests: persist enough to rejoin the host after a drop
        // or an app restart (the host holds the authoritative match).
        if (!app.lanHostBase) {
            acquireLanHostLock();
        } else {
            saveLanSession({ hostBase: app.lanHostBase, matchId, seed, playerId, decks });
        }
        try {
            await refresh();
        } catch (error) {
            // The host may just be momentarily unreachable (Wi-Fi settling,
            // app resuming). Don't bail to the menu — reconnect in place.
            startReconnect();
        }
    }

    // Tear down LAN state (leaving the match / going home). Safe to call when
    // no LAN game is active.
    function endLanGame() {
        clearLanPoll();
        stopReconnect();
        releaseLanHostLock();
        clearLanSession();
        app.lanGame = false;
        app.lanHostBase = null;
        app.lanPollFails = 0;
        setLanHostBase(null);
        app.humanPlayerId = 1;
    }

    // --- LAN reconnect -------------------------------------------------------
    // A guest drives the authoritative match on the host over the network, so a
    // dropped Wi-Fi, a backgrounded app, or a host that briefly slept all show
    // up as a failed call. Rather than losing the match we surface a
    // "reconnecting" overlay and re-fetch the host's state with backoff until it
    // answers — the state is authoritative, so whatever happened while we were
    // gone (including our own last move) is reflected once we're back.
    const LAN_SESSION_KEY = 'mytcg_lan_session';
    const LAN_SESSION_TTL_MS = 6 * 60 * 60 * 1000; // stale rejoin offers expire

    function saveLanSession(session) {
        try {
            localStorage.setItem(LAN_SESSION_KEY, JSON.stringify({ ...session, savedAt: Date.now() }));
        } catch (error) { /* storage unavailable: reconnect-in-session still works */ }
    }
    function clearLanSession() {
        try { localStorage.removeItem(LAN_SESSION_KEY); } catch (error) { /* ignore */ }
    }
    function loadLanSession() {
        try {
            const raw = localStorage.getItem(LAN_SESSION_KEY);
            if (!raw) return null;
            const session = JSON.parse(raw);
            if (!session || !session.matchId || !session.hostBase) return null;
            if (Date.now() - (session.savedAt || 0) > LAN_SESSION_TTL_MS) {
                clearLanSession();
                return null;
            }
            return session;
        } catch (error) {
            return null;
        }
    }

    function setReconnectOverlay(visible, message) {
        if (!ui.reconnectOverlay) return;
        ui.reconnectOverlay.classList.toggle('hidden', !visible);
        ui.reconnectOverlay.setAttribute('aria-hidden', String(!visible));
        if (message && ui.reconnectStatus) ui.reconnectStatus.textContent = message;
    }

    function startReconnect() {
        if (!isLanGame() || app.lanReconnecting) return;
        app.lanReconnecting = true;
        clearLanPoll();
        setReconnectOverlay(true, 'Reconnecting to the game…');
        reconnectTick(1000);
    }

    async function reconnectTick(delay) {
        if (!app.lanReconnecting) return;
        let snapshot;
        try {
            const c = cfg();
            const data = await postJson('/api/state', {
                match_id: c.match_id, player_id: c.player_id, seed: c.seed,
                deck_a: c.deck_a, deck_b: c.deck_b, deck_a_cards: c.deck_a_cards, decks: c.decks,
            });
            snapshot = data.snapshot;
        } catch (error) {
            const next = Math.min(delay * 2, 5000);
            app.lanReconnectTimer = setTimeout(() => reconnectTick(next), delay);
            return;
        }
        // Host answered — clear the reconnecting flag *before* rendering so the
        // resumed render can reschedule polling (scheduleLanPoll is suppressed
        // while reconnecting). rerender runs maybeAutoAdvance, which restarts the
        // normal turn/poll flow from the freshly authoritative state.
        app.lanReconnecting = false;
        app.lanPollFails = 0;
        if (app.lanReconnectTimer) { clearTimeout(app.lanReconnectTimer); app.lanReconnectTimer = null; }
        setReconnectOverlay(false);
        flashStatus('Reconnected.');
        rerender(snapshot);
    }

    function stopReconnect() {
        app.lanReconnecting = false;
        if (app.lanReconnectTimer) { clearTimeout(app.lanReconnectTimer); app.lanReconnectTimer = null; }
        setReconnectOverlay(false);
    }

    function openSheet(modal) {
        modal.classList.add('open');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeSheet(modal) {
        modal.classList.remove('open');
        modal.setAttribute('aria-hidden', 'true');
    }

    function init(options = {}) {
        onExitToMenu = options.onExitToMenu || null;
        if (ui.btnHome) {
            ui.btnHome.onclick = () => {
                endLanGame();
                if (onExitToMenu) onExitToMenu();
            };
        }
        if (ui.btnReconnectLeave) {
            // Give up on a match we can't reconnect to and go home.
            ui.btnReconnectLeave.onclick = () => {
                endLanGame();
                if (onExitToMenu) onExitToMenu();
            };
        }
        ui.btnHistory.onclick = () => {
            openSheet(ui.historyModal);
        };
        ui.btnCloseSettings.onclick = () => {
            closeSheet(ui.settingsModal);
        };
        ui.btnCloseHistory.onclick = () => {
            closeSheet(ui.historyModal);
        };
        ui.btnSurrender.onclick = () => {
            openSheet(ui.surrenderModal);
        };
        ui.btnCloseSurrender.onclick = () => {
            closeSheet(ui.surrenderModal);
        };
        ui.btnSurrenderCancel.onclick = () => {
            closeSheet(ui.surrenderModal);
        };
        ui.btnSurrenderConfirm.onclick = () => {
            onSurrender();
        };
        ui.btnCloseEndTurn.onclick = () => {
            closeSheet(ui.endTurnModal);
        };
        ui.btnEndTurnCancel.onclick = () => {
            closeSheet(ui.endTurnModal);
        };
        ui.btnEndTurnConfirm.onclick = async () => {
            closeSheet(ui.endTurnModal);
            if (app.snapshot) await endTurnNow(app.snapshot);
        };
        [ui.settingsModal, ui.historyModal, ui.surrenderModal, ui.endTurnModal].forEach((modal) => {
            modal.addEventListener('click', (event) => {
                if (event.target === modal) closeSheet(modal);
            });
        });
        ui.btnNewGame.onclick = () => {
            // Debug path: the settings-sheet deck dropdowns take over from
            // whatever the main menu picked (always a 1v1).
            app.deckAName = null;
            app.deckBName = null;
            app.deckACards = null;
            app.deckNames = null;
            app.aiPlayerIds = [app.aiPlayerId];
            app.statsMeta = null;
            closeSheet(ui.settingsModal);
            newGame();
        };
        ui.btnEndTurn.onclick = () => {
            onEndTurn();
        };
        initEmotes();
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

        // No initial refresh: the app opens on the main menu and the first
        // match is created by its Play button.
    }

    // Used by the history-based back navigation: a live match intercepts the
    // hardware back button with the surrender prompt instead of exiting.
    function isMatchLive() {
        return Boolean(app.snapshot && app.snapshot.phase !== 'GAME_OVER');
    }

    // Before the player has even submitted their mulligan the match hasn't
    // really begun: leaving is free (no surrender, no game_result, no stats).
    function canQuitFree() {
        if (!isMatchLive()) return false;
        const history = (app.snapshot && app.snapshot.action_history) || [];
        const prefix = `mulligan_keep:${cfg().player_id}:`;
        return !history.some((entry) => String(entry).startsWith(prefix));
    }

    function promptSurrender() {
        openSheet(ui.surrenderModal);
    }

    return {
        init,
        startGame,
        startLanGame,
        endLanGame,
        isLanGame,
        isMatchLive,
        canQuitFree,
        promptSurrender,
        // The menu offers a "Reconnect" entry when an unclean exit left a live
        // guest session behind; it rejoins by feeding this back to startLanGame.
        loadLanSession,
        // For the in-game trade UI: who we are and which match we're in.
        lanContext: () => ({
            hostBase: app.lanHostBase,
            matchId: app.matchId,
            playerId: cfg().player_id,
            players: (app.snapshot && app.snapshot.players) || [],
        }),
    };
}
