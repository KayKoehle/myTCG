import {
    buildCardNameMap,
    cardArtTag,
    cardPngUrl,
    describeChoiceOption,
    displayCardTitle,
    effectLabel,
    escapeHtml,
    fillSelectFromOptions,
    findCardById,
    handTitleScale,
    humanLegalActions,
    laneLabel,
    ordinalLabel,
    stackPower,
    statChangeClass,
    typeLabel,
} from './helpers.js';

// Action verbs for the card-stack selection popup, keyed by the engine's
// pending choice_kind. Falls back to a generic "Choose".
const CHOICE_VERB_BY_KIND = {
    banish_enemy: 'Banish',
    banish_other_friendly: 'Banish',
    banish_friendly_for_inanna: 'Banish',
    banish_two_enemies: 'Banish',
    ishtar_banish_small_enemy: 'Banish',
    slave_banish_for_artifact_discount: 'Banish',
    destroy_enemy_here: 'Destroy',
    greek_soldiers_destroy_weaklings: 'Destroy',
    discard_from_hand: 'Discard',
    return_human_to_hand: 'Return',
    revive_underworld_here: 'Revive',
    revive_choose_location: 'Revive at',
    put_hand_to_underworld: 'Bury',
    namtar_send_to_underworld: 'Send',
    move_hero_to_here: 'Move',
    farmer_free_human: 'Free',
    enkidu_join_gilgamesh: 'Join',
    trojan_horse_payload: 'Send',
    fisherman_draw_two_humans: 'Draw',
    tutor_from_deck: 'Draw',
    calchas_pick: 'Draw',
    dolon_bottom_top_card: 'Bury',
};

// Decide whether a pending choice is a simple "pick one card" selection that
// the card-stack popup can present. Options that encode a target (contain '|',
// e.g. moves) or that resolve to no cards fall back to the legacy text modal.
function classifyCardChoice(pending, snapshot, cardNameById) {
    const cardEntries = [];
    const extraEntries = [];
    for (const opt of (pending.options || [])) {
        if (typeof opt !== 'string') return null;
        if (opt.includes('|')) return null;
        const card = findCardById(snapshot, opt);
        if (card) {
            cardEntries.push({ card, option: opt });
        } else {
            extraEntries.push({ value: opt, label: describeChoiceOption(opt, cardNameById) });
        }
    }
    if (cardEntries.length === 0) return null;
    return { mode: 'select', cardEntries, extras: extraEntries };
}

// Dolon's "look at the top card, then choose to bury it" choice encodes the
// revealed card as "BOTTOM|<id>" so the player can both see it and bury it —
// never just a bare id or raw text.
function classifyDolonChoice(pending, snapshot) {
    if (pending.choice_kind !== 'dolon_bottom_top_card') return null;
    const revealOption = (pending.options || []).find((opt) => typeof opt === 'string' && opt.startsWith('BOTTOM|'));
    if (!revealOption) return null;
    const cardId = revealOption.split('|')[1];
    const card = findCardById(snapshot, cardId);
    if (!card) return null;
    return {
        mode: 'select',
        cardEntries: [{ card, option: revealOption }],
        extras: [{ value: 'PASS', label: 'Leave it on top' }],
    };
}

// A "pick some cards" choice whose options are pipe-joined combinations of
// card ids (Bull of Heaven's two banishes, the Trojan Horse payload, Greek
// Soldiers, tutoring more than one named card, ...). Instead of listing every
// precomputed combination, show each candidate card once and let the player
// toggle a selection until it matches one of the engine's valid combos.
function classifyMultiCardChoice(pending, snapshot) {
    const options = pending.options || [];
    const real = options.filter((opt) => opt !== 'PASS' && opt !== 'NONE');
    if (real.length === 0) return null;
    const hasCombo = real.some((opt) => typeof opt === 'string' && opt.includes('|'));
    if (!hasCombo) return null; // a plain single-card list is handled by classifyCardChoice instead

    const idOrder = [];
    const seen = new Set();
    for (const opt of real) {
        if (typeof opt !== 'string') return null;
        for (const part of opt.split('|')) {
            if (!findCardById(snapshot, part)) return null; // not a pure card combo (e.g. a move option)
            if (!seen.has(part)) {
                seen.add(part);
                idOrder.push(part);
            }
        }
    }

    const cardEntries = idOrder.map((id) => ({ card: findCardById(snapshot, id) }));
    const extras = [];
    if (options.includes('NONE')) extras.push({ value: 'NONE', label: 'None' });
    if (options.includes('PASS')) extras.push({ value: 'PASS', label: 'Pass' });
    return { mode: 'multi-select', cardEntries, comboOptions: real, extras };
}

function renderCards(cards, options = {}) {
    const { movableCards = new Set(), synergyCards = new Set(), abilityCards = new Set(), emptyAsCardSlot = false } = options;
    if (!cards || cards.length === 0) {
        return emptyAsCardSlot
            ? '<div class="empty-card-slot-wrap"><div class="empty-card-slot" aria-hidden="true"></div></div>'
            : '';
    }
    return cards.map((c) => {
        const powerClass = statChangeClass(c.power, c.base_power, true);
        return `
        <div class="card ${c.id && movableCards.has(c.id) ? 'movable-choice' : ''} ${c.id && synergyCards.has(c.id) ? 'synergy-ref' : ''} ${c.id && abilityCards.has(c.id) ? 'ability-ready' : ''} ${c.while_top_active ? 'while-top-active' : ''}" ${c.id ? `data-board-card-id="${c.id}"` : ''}>
            <div class="card-headline">
                <span class="stat-badge cost">${c.cost ?? '?'}</span>
                <div class="card-title">${c.name}</div>
                <span class="stat-badge power ${powerClass}">${c.power !== null ? c.power : '?'}</span>
            </div>
            ${typeLabel(c) ? `<div class="card-type">${escapeHtml(typeLabel(c))}</div>` : ''}
            <div class="card-media">
                ${cardArtTag(c.name, 'card-art')}
            </div>
            <div class="card-body">
                <div class="tiny">${effectLabel(c)}</div>
            </div>
        </div>
    `;
    }).join('');
}

function renderVpTrack(vpValue) {
    const totalDots = Math.max(3, Number(vpValue) || 0);
    const filled = Number(vpValue) || 0;
    const crownIcon = (isFilled) => {
        const fill = isFilled ? '#ffc84f' : 'rgba(255,255,255,0.12)';
        const highlight = isFilled ? '#fff4b3' : 'rgba(255,255,255,0.22)';
        const stroke = isFilled ? 'rgba(255,236,166,0.92)' : 'rgba(255,255,255,0.35)';
        const glow = isFilled ? 'filter="drop-shadow(0 0 3px rgba(255,200,79,0.45))"' : '';
        return `<svg class="vp-crown" viewBox="0 0 24 18" aria-hidden="true" ${glow}>
            <path d="M2 14 L4 6 L8 10 L12 3 L16 10 L20 6 L22 14 Z" fill="${fill}" stroke="${stroke}" stroke-width="1.2"/>
            <path d="M5 12 L8.5 9.2 L12 5.5 L15.5 9.2 L19 12" fill="none" stroke="${highlight}" stroke-width="1" opacity="0.8"/>
            <rect x="2" y="14" width="20" height="2.8" rx="1" fill="${fill}" stroke="${stroke}" stroke-width="1.1"/>
        </svg>`;
    };
    return `<div class="vp-track">${Array.from({ length: totalDots }, (_, i) => `<span class="vp-dot">${crownIcon(i < filled)}</span>`).join('')}</div>`;
}

function renderLaneSlots(filledCount, totalSlots = 7) {
    const filled = Math.max(0, Math.min(totalSlots, Number(filledCount) || 0));
    const slots = Array.from({ length: totalSlots }, (_, i) => `<span class="lane-slot ${i < filled ? 'filled' : ''}"></span>`).join('');
    return `<div class="lane-capacity"><div class="lane-slots">${slots}</div><span class="lane-capacity-text">${filled}/${totalSlots}</span></div>`;
}

function renderManaTrack(capValue, availableValue) {
    const cap = Math.max(0, Number(capValue) || 0);
    const available = Math.max(0, Math.min(cap, Number(availableValue) || 0));
    const totalSlots = 7;
    return `<div class="mana-track">${Array.from({ length: totalSlots }, (_, i) => {
        if (i < available) return '<span class="mana-gem full"></span>';
        if (i < cap) return '<span class="mana-gem used"></span>';
        return '<span class="mana-gem empty"></span>';
    }).join('')}</div>`;
}

function renderOpponentHand(cardCount, revealedCards) {
    // When a card like Sinon forces the opponent to play open-handed, the
    // snapshot includes their actual cards; show the art instead of card backs.
    if (Array.isArray(revealedCards) && revealedCards.length > 0) {
        return revealedCards
            .map((card) => `<img class="opp-card-back opp-card-revealed" src="${cardPngUrl(card.name)}" alt="${escapeHtml(card.name)}" draggable="false" loading="lazy" onerror="this.style.display='none';">`)
            .join('');
    }
    const count = Math.max(0, Number(cardCount) || 0);
    if (count === 0) return '<div class="tiny">No cards</div>';
    return Array.from({ length: count }, () => '<div class="opp-card-back" aria-hidden="true"></div>').join('');
}

function renderUnderworldStack(cards, isOpp) {
    const list = Array.isArray(cards) ? cards : [];
    const count = list.length;
    if (count === 0) {
        return '<div class="empty-card-slot" aria-hidden="true"></div>';
    }
    const top = list[count - 1];
    const topCard = top && top.name
        ? `<img class="underworld-top-art" src="${cardPngUrl(top.name)}" alt="${escapeHtml(top.name)}" loading="lazy" onerror="this.style.display='none';">`
        : '';

    return `
        <div class="deck-stack underworld-stack${isOpp ? ' deck-stack-opp' : ''}">
            <span class="deck-card"></span>
            <span class="deck-card"></span>
            <span class="underworld-top-card">${topCard}</span>
        </div>
    `;
}

function renderActionHistory(snapshot, ui, config) {
    const history = Array.isArray(snapshot.action_history_pretty)
        ? snapshot.action_history_pretty
        : (Array.isArray(snapshot.action_history) ? snapshot.action_history : []);
    const rawHistory = Array.isArray(snapshot.action_history) ? snapshot.action_history : [];
    const human = String(config.player_id);
    const ai = String(config.ai_player_id);

    const relabelPlayers = (text) => String(text)
        .replaceAll(`P${human}`, 'You')
        .replaceAll(`P${ai}`, 'Opponent');

    if (!history.length) {
        ui.actionHistory.innerHTML = '<div class="history-empty">No actions yet.</div>';
        return;
    }

    const groups = [];
    let drawCount = 0;
    let currentGroup = null;
    let gameResult = '';
    const crownCounts = { [human]: 0, [ai]: 0 };

    const pushCurrentGroup = () => {
        // Keep the group even with zero items: a turn where the player did
        // nothing but draw and end still deserves its "Round X - ..." entry.
        if (!currentGroup) return;
        groups.push(currentGroup);
        currentGroup = null;
    };

    for (let i = 0; i < history.length; i++) {
        const pretty = relabelPlayers(history[i]);
        const raw = rawHistory[i] || '';
        const parts = String(raw).split(':');
        const kind = parts[0];

        if (kind === 'mulligan_select' || kind === 'mulligan_keep') {
            let mulliganGroup = groups.find((g) => g.title === 'Mulligan');
            if (!mulliganGroup) {
                mulliganGroup = { title: 'Mulligan', items: [] };
                groups.push(mulliganGroup);
            }
            mulliganGroup.items.push(pretty);
            continue;
        }

        if (kind === 'draw_card') {
            pushCurrentGroup();
            drawCount += 1;
            const roundNumber = Math.ceil(drawCount / 2);
            const playerId = parts[1] || '';
            const turnLabel = playerId === human ? 'Your turn' : 'Opponent turn';
            currentGroup = {
                title: `Round ${roundNumber} - ${turnLabel}`,
                items: [],
                result: '',
            };
            continue;
        }

        if (kind === 'end_turn') {
            continue;
        }

        if (kind === 'round_result') {
            const roundNo = parts[1] || '?';
            const winner = parts[2] || '';
            const resultLabel = winner === 'DRAW'
                ? `Round ${roundNo}: Draw (no crown)`
                : (() => {
                    crownCounts[winner] = (crownCounts[winner] || 0) + 1;
                    const owner = winner === human ? 'You' : 'Opponent';
                    const crownNo = ordinalLabel(crownCounts[winner]);
                    return `Round ${roundNo}: ${owner} gained ${owner === 'You' ? 'your' : 'their'} ${crownNo} crown`;
                })();
            if (currentGroup) {
                currentGroup.result = resultLabel;
            } else if (groups.length) {
                groups[groups.length - 1].result = resultLabel;
            }
            continue;
        }

        if (kind === 'game_result') {
            const winner = parts[1] || '';
            gameResult = winner === 'DRAW'
                ? 'Game ended in a draw'
                : `${winner === human ? 'You' : 'Opponent'} won the game`;
            continue;
        }

        if (!currentGroup) {
            currentGroup = {
                title: 'Round ? - Turn',
                items: [],
                result: '',
            };
        }
        currentGroup.items.push(pretty);
    }
    pushCurrentGroup();

    // Keep the full play history (no cap): the history panel scrolls.
    const groupedMarkup = groups
        .slice()
        .reverse()
        .map((group) => `
            <section class="history-group">
                <div class="history-group-title">${escapeHtml(group.title)}</div>
                <div class="history-group-items">
                    ${group.items.length
                        ? group.items.map((entry) => `<div class="history-item">${escapeHtml(entry)}</div>`).join('')
                        : '<div class="history-item history-item-empty">They did not do anything.</div>'}
                    ${group.result ? `<div class="history-round-result">${escapeHtml(group.result)}</div>` : ''}
                </div>
            </section>
        `)
        .join('');

    ui.actionHistory.innerHTML = `${groupedMarkup}${gameResult ? `<div class="history-game-result">${escapeHtml(gameResult)}</div>` : ''}`;
}

export function layoutHand(ui) {
    const handEl = ui.hand;
    if (!handEl) return;
    const cards = Array.from(handEl.querySelectorAll('.hand-card[data-card-id]'));
    if (!cards.length) {
        // An empty hand keeps its space (the CSS min-height stays in charge)
        // so the layout doesn't jump when the last card is played.
        handEl.style.minHeight = '';
        return;
    }

    // Card width is set in CSS (smaller on phones), so measure it here.
    const cardWidth = cards[0].getBoundingClientRect().width || 170;
    const availableWidth = Math.max(cardWidth, handEl.clientWidth || cardWidth);
    const totalSpread = Math.max(0, availableWidth - cardWidth);
    const step = cards.length === 1
        ? 0
        : Math.max(20, Math.min(cardWidth + 8, totalSpread / (cards.length - 1)));

    cards.forEach((cardEl, index) => {
        cardEl.style.left = `${Math.round(index * step)}px`;
        cardEl.style.zIndex = String(index + 1);
    });

    const cardHeight = cards[0].getBoundingClientRect().height || 218;
    handEl.style.minHeight = `${Math.ceil(cardHeight + 12)}px`;
}

// The End Turn button doubles as "New Game" (game over), "Confirm mulligan"
// (opening mulligan), and a disabled "Opponent's Turn" indicator while the AI
// is acting. Kept in one place so the controller can re-apply it mid-flip.
export function updateEndTurnButton(ui, app, config) {
    const snapshot = app.snapshot;
    if (!snapshot) return;
    const isGameOver = snapshot.phase === 'GAME_OVER';
    const isOpeningMulligan = snapshot.phase === 'MULLIGAN'
        && snapshot.pending_choice
        && snapshot.pending_choice.choice_kind === 'opening_mulligan';
    const canActMulligan = isOpeningMulligan && Number(snapshot.pending_choice.player_id) === config.player_id;
    const opponentTurn = app.opponentTurnActive && !isGameOver;
    const legal = humanLegalActions(snapshot, config.player_id);

    if (opponentTurn) {
        ui.btnEndTurn.disabled = true;
        ui.btnEndTurn.textContent = "Opponent's Turn";
    } else {
        ui.btnEndTurn.disabled = isGameOver ? false : !(canActMulligan || legal.some((a) => a.kind === 'end_turn'));
        ui.btnEndTurn.textContent = isGameOver ? 'Main Menu' : (isOpeningMulligan ? 'Confirm mulligan' : 'End Turn');
    }
    ui.btnEndTurn.classList.toggle('mulligan-confirm', Boolean(isOpeningMulligan) && !opponentTurn);
    ui.btnEndTurn.classList.toggle('opponent-turn', opponentTurn);
    ui.btnEndTurn.classList.toggle('new-game', isGameOver && !opponentTurn);
}

export function renderSnapshot({ snapshot, ui, app, config, onChooseOption, cardStack }) {
    app.snapshot = snapshot;
    app.cardNameById = buildCardNameMap(snapshot);

    const human = String(config.player_id);
    const ai = String(config.ai_player_id);
    const current = String(snapshot.current_player_id);
    const vp = snapshot.victory_points;
    // Engine-internal side indexes (options like "card|loc|side" use them):
    // stacks/vp dicts are keyed by player id in side order, so the key
    // position is the side index.
    const playerIds = Object.keys(vp || { '1': 0, '2': 0 });
    const humanSideIdx = Math.max(0, playerIds.indexOf(human));
    const mana = snapshot.mana_pool;
    const manaCap = snapshot.mana_cap || {};
    const deckSizes = snapshot.deck_sizes || {};
    const legal = humanLegalActions(snapshot, config.player_id);

    app.playableCardSet = new Set(
        legal
            .filter((a) => a.kind === 'play_card' && a.card_id != null)
            .map((a) => a.card_id)
    );
    app.abilityReadyCardSet = new Set(
        legal
            .filter((a) => a.kind === 'use_ability' && a.card_id != null)
            .map((a) => a.card_id)
    );
    app.legalMoveChoiceSet = new Set();
    app.movableChoiceCardSet = new Set();

    if (snapshot.pending_choice && Number(snapshot.pending_choice.player_id) === config.player_id) {
        for (const opt of (snapshot.pending_choice.options || [])) {
            if (typeof opt !== 'string' || opt === 'PASS') continue;
            const parts = opt.split('|');
            if (parts.length === 3) {
                const cardId = parts[0];
                const loc = Number(parts[1]);
                const side = Number(parts[2]);
                app.legalMoveChoiceSet.add(`${cardId}|${loc}|${side}`);
                app.movableChoiceCardSet.add(cardId);
            }
        }
    }

    const isOpeningMulligan = snapshot.phase === 'MULLIGAN'
        && snapshot.pending_choice
        && snapshot.pending_choice.choice_kind === 'opening_mulligan';
    const canActMulligan = isOpeningMulligan && Number(snapshot.pending_choice.player_id) === config.player_id;

    fillSelectFromOptions(ui.deckA, snapshot.available_decks || [], ui.deckA.value || app.defaultDeckA);
    fillSelectFromOptions(ui.deckB, snapshot.available_decks || [], ui.deckB.value || app.defaultDeckB);

    // Without checkpoints (e.g. the bundled Android build) the dropdown is
    // useless, so hide the whole field.
    const checkpoints = snapshot.available_checkpoints || [];
    ui.checkpointField.classList.toggle('hidden', checkpoints.length === 0);
    fillSelectFromOptions(
        ui.checkpointPath,
        checkpoints,
        ui.checkpointPath.value || 'stats/checkpoints/ai_nn_distributed_latest.pt'
    );

    ui.hud.innerHTML = '';

    ui.scorePanel.innerHTML = [
        `<div class="score-side"><span class="score-name">You</span>${renderVpTrack(vp[human] ?? 0)}</div>`,
        '<div class="score-divider"></div>',
        `<div class="score-side"><span class="score-name">Opp</span>${renderVpTrack(vp[ai] ?? 0)}</div>`,
    ].join('');

    ui.oppMana.innerHTML = renderManaTrack(manaCap[ai] ?? 0, mana[ai] ?? 0);
    ui.yourMana.innerHTML = renderManaTrack(manaCap[human] ?? 0, mana[human] ?? 0);
    ui.oppHand.innerHTML = renderOpponentHand(snapshot.opponent_hand_size, snapshot.opponent_hand_revealed ? snapshot.opponent_hand : null);
    if (ui.oppHandCount) {
        ui.oppHandCount.textContent = String(Math.max(0, Number(snapshot.opponent_hand_size) || 0));
    }

    // Live synergies: hand cards whose "if" clause is fulfilled, and the
    // cards elsewhere that fulfil it (board cards or the own underworld).
    const handSynergies = snapshot.hand_synergies || {};
    const synergyHandSet = new Set(Object.keys(handSynergies));
    const synergyRefSet = new Set();
    for (const partners of Object.values(handSynergies)) {
        for (const partnerId of (partners || [])) synergyRefSet.add(partnerId);
    }

    const underworld = snapshot.underworld || {};
    ui.oppUnderworld.innerHTML = renderUnderworldStack(underworld[ai] || [], true);
    ui.yourUnderworld.innerHTML = renderUnderworldStack(underworld[human] || []);
    const underworldSynergy = (underworld[human] || []).some((card) => card && synergyRefSet.has(card.id));
    ui.yourUnderworld.classList.toggle('synergy-glow', underworldSynergy);
    ui.oppUnderworldCount.textContent = String((underworld[ai] || []).length);
    ui.yourUnderworldCount.textContent = String((underworld[human] || []).length);

    ui.oppDeckCount.textContent = String(deckSizes[ai] ?? 0);
    ui.yourDeckCount.textContent = String(deckSizes[human] ?? 0);

    // A simple "pick one card" choice for the human is presented in the card
    // stack popup (tap to inspect, then a confirm button like "Banish Achilles").
    const cardChoice = (snapshot.pending_choice
        && !isOpeningMulligan
        && Number(snapshot.pending_choice.player_id) === config.player_id
        && cardStack)
        ? (classifyDolonChoice(snapshot.pending_choice, snapshot)
            || classifyCardChoice(snapshot.pending_choice, snapshot, app.cardNameById)
            || classifyMultiCardChoice(snapshot.pending_choice, snapshot))
        : null;

    if (cardChoice) {
        const p = snapshot.pending_choice;
        ui.pending.innerHTML = '';
        ui.choiceModal.classList.remove('open');
        ui.choiceModal.setAttribute('aria-hidden', 'true');
        ui.choiceOptions.innerHTML = '';
        cardStack.open({
            mode: cardChoice.mode,
            title: p.prompt || p.choice_kind,
            confirmVerb: CHOICE_VERB_BY_KIND[p.choice_kind] || 'Choose',
            cards: cardChoice.cardEntries,
            extras: cardChoice.extras,
            comboOptions: cardChoice.comboOptions,
            onConfirm: (optionId) => onChooseOption(optionId),
            onExtra: (value) => onChooseOption(value),
        });
    } else if (snapshot.pending_choice && !isOpeningMulligan) {
        if (cardStack) cardStack.close();
        const p = snapshot.pending_choice;
        const canChoose = Number(p.player_id) === config.player_id && p.choice_kind !== 'opening_mulligan';
        const listedOptions = (p.options || []).map((opt) => ({
            value: opt,
            label: describeChoiceOption(opt, app.cardNameById, humanSideIdx),
        }));
        // The old orange "Pending choice ..." banner is gone: the human sees
        // the choice modal / card-stack popup, the opponent's choices resolve
        // silently.
        ui.pending.innerHTML = '';

        if (canChoose) {
            ui.choiceTitle.textContent = 'Choice for You';
            ui.choicePrompt.textContent = p.prompt || p.choice_kind;
            ui.choiceSub.textContent = 'Pick one option.';
            const dismissButton = app.legalMoveChoiceSet.size > 0
                ? '<button class="choice-option-btn choice-dismiss" data-choice-dismiss>Pick on the board (drag the highlighted card to a lane)</button>'
                : '';
            ui.choiceOptions.innerHTML = listedOptions
                .map((opt) => `<button class="choice-option-btn" data-choice-option="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</button>`)
                .join('') + dismissButton;
            ui.choiceOptions.querySelectorAll('[data-choice-option]').forEach((el) => {
                el.addEventListener('click', () => {
                    const optionId = el.getAttribute('data-choice-option');
                    if (!optionId) return;
                    onChooseOption(optionId);
                });
            });
            const dismissEl = ui.choiceOptions.querySelector('[data-choice-dismiss]');
            if (dismissEl) {
                dismissEl.addEventListener('click', () => {
                    ui.choiceModal.classList.remove('open');
                    ui.choiceModal.setAttribute('aria-hidden', 'true');
                });
            }
            ui.choiceModal.classList.add('open');
            ui.choiceModal.setAttribute('aria-hidden', 'false');
        } else {
            ui.choiceModal.classList.remove('open');
            ui.choiceModal.setAttribute('aria-hidden', 'true');
            ui.choiceOptions.innerHTML = '';
        }
    } else {
        if (cardStack) cardStack.close();
        ui.pending.innerHTML = '';
        ui.choiceModal.classList.remove('open');
        ui.choiceModal.setAttribute('aria-hidden', 'true');
        ui.choiceOptions.innerHTML = '';
    }

    const opp = playerIds.find((p) => p !== human) || (human === '1' ? '2' : '1');

    ui.lanes.innerHTML = snapshot.locations.map((loc) => {
        const oppCards = loc.stacks[opp] || [];
        const yourCards = loc.stacks[human] || [];
        const laneCardCount = oppCards.length + yourCards.length;
        const oppPower = stackPower(oppCards);
        const yourPower = stackPower(yourCards);
        const laneLeadClass = yourPower > oppPower ? 'lead-you' : (oppPower > yourPower ? 'lead-opp' : '');

        return `
            <article class="lane">
                <div class="lane-head">
                    <div class="lane-head-left">${renderLaneSlots(laneCardCount, 7)}</div>
                </div>
                <div class="lane-row lane-row-opp" data-location-id="${loc.location_id}">
                    <div class="stack-cards" data-count="${oppCards.length}">${renderCards(oppCards, { synergyCards: synergyRefSet })}</div>
                </div>
                <div class="lane-mid">
                    <span class="lane-score ${laneLeadClass}"><span class="opp">${oppPower}</span> / <span class="you">${yourPower}</span></span>
                </div>
                <div class="lane-row lane-drop" data-location-id="${loc.location_id}">
                    <div class="stack-cards" data-count="${yourCards.length}">${renderCards(yourCards, { movableCards: app.movableChoiceCardSet, synergyCards: synergyRefSet, abilityCards: app.abilityReadyCardSet })}</div>
                </div>
            </article>
        `;
    }).join('');

    ui.hand.innerHTML = snapshot.hand.length
        ? snapshot.hand.map((c) => {
            const handTitle = displayCardTitle(c.name);
            const titleScale = handTitleScale(c.name);
            const isPlayable = !isOpeningMulligan && app.playableCardSet.has(c.id);
            const isUnplayable = !isOpeningMulligan && !app.playableCardSet.has(c.id);
            const hasSynergy = !isOpeningMulligan && synergyHandSet.has(c.id);
            const costClass = statChangeClass(c.cost, c.base_cost, false);
            return `
                <div class="hand-card ${isOpeningMulligan ? 'mulligan-mode' : ''} ${app.mulliganSelected.has(c.id) ? 'marked' : ''} ${isPlayable ? 'playable' : ''} ${isUnplayable ? 'unplayable' : ''} ${hasSynergy ? 'synergy' : ''}" data-card-id="${c.id}">
                    <div class="hand-card-headline">
                        <span class="stat-badge cost ${costClass}">${c.cost ?? '?'}</span>
                        <div class="hand-title-main" style="--title-scale: ${titleScale};"><span class="hand-title-text">${escapeHtml(handTitle)}</span></div>
                        <span class="stat-badge power">${c.power !== null ? c.power : '?'}</span>
                    </div>
                    ${typeLabel(c) ? `<div class="card-type">${escapeHtml(typeLabel(c))}</div>` : ''}
                    <div class="hand-media">
                        ${cardArtTag(c.name, 'hand-art')}
                    </div>
                    <div class="hand-body">
                        <div class="tiny">${effectLabel(c)}</div>
                    </div>
                    <div class="mulligan-x">X</div>
                </div>
            `;
        }).join('')
        : '';

    // The mulligan instruction banner was removed; mulligan mode is now shown
    // purely by the red X marks on cards and the teal "Confirm mulligan" button.
    if (!isOpeningMulligan) {
        app.mulliganSelected.clear();
    }

    updateEndTurnButton(ui, app, config);

    // The status line only surfaces transient warnings and errors; game over
    // is announced by its own animation and the New Game button.
    ui.status.textContent = '';

    renderActionHistory(snapshot, ui, config);
}
