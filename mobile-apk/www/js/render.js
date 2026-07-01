import {
    buildCardNameMap,
    cardArtTag,
    cardPngUrl,
    describeChoiceOption,
    displayCardTitle,
    effectLabel,
    escapeHtml,
    fillSelectFromOptions,
    gameOverStatusText,
    handTitleScale,
    humanLegalActions,
    laneLabel,
    ordinalLabel,
    stackPower,
} from './helpers.js';

function renderCards(cards, options = {}) {
    const { movableCards = new Set(), emptyAsCardSlot = false } = options;
    if (!cards || cards.length === 0) {
        return emptyAsCardSlot
            ? '<div class="empty-card-slot-wrap"><div class="empty-card-slot" aria-hidden="true"></div></div>'
            : '';
    }
    return cards.map((c) => `
        <div class="card ${c.id && movableCards.has(c.id) ? 'movable-choice' : ''}" ${c.id ? `data-board-card-id="${c.id}"` : ''}>
            <div class="card-headline">
                <span class="stat-badge cost">${c.cost ?? '?'}</span>
                <div class="card-title">${c.name}</div>
                <span class="stat-badge power">${c.power !== null ? c.power : '?'}</span>
            </div>
            <div class="card-media">
                ${cardArtTag(c.name, 'card-art')}
            </div>
            <div class="card-body">
                <div class="tiny">${effectLabel(c)}</div>
            </div>
        </div>
    `).join('');
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

function renderOpponentHand(cardCount) {
    const count = Math.max(0, Number(cardCount) || 0);
    if (count === 0) return '<div class="tiny">No cards</div>';
    return Array.from({ length: count }, () => '<div class="opp-card-back" aria-hidden="true"></div>').join('');
}

function renderUnderworldStack(cards) {
    const list = Array.isArray(cards) ? cards : [];
    const count = list.length;
    if (count === 0) {
        return `
            <div class="underworld-stack-shell">
                <div class="empty-card-slot-wrap"><div class="empty-card-slot" aria-hidden="true"></div></div>
                <div class="pile-count">0</div>
            </div>
        `;
    }
    const top = count ? list[count - 1] : null;
    const topCard = top && top.name
        ? `<img class="underworld-top-art" src="${cardPngUrl(top.name)}" alt="${escapeHtml(top.name)}" loading="lazy" onerror="this.style.display='none';">`
        : '';

    return `
        <div class="underworld-stack-shell">
            <div class="deck-stack underworld-stack">
                <span class="deck-card"></span>
                <span class="deck-card"></span>
                <span class="underworld-top-card">${topCard}</span>
            </div>
            <div class="pile-count">${count}</div>
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
        if (!currentGroup || !currentGroup.items.length) return;
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

    const groupedMarkup = groups
        .slice(-12)
        .reverse()
        .map((group) => `
            <section class="history-group">
                <div class="history-group-title">${escapeHtml(group.title)}</div>
                <div class="history-group-items">
                    ${group.items.map((entry) => `<div class="history-item">${escapeHtml(entry)}</div>`).join('')}
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
        handEl.style.minHeight = '0';
        return;
    }

    const cardWidth = 170;
    const availableWidth = Math.max(cardWidth, handEl.clientWidth || cardWidth);
    const totalSpread = Math.max(0, availableWidth - cardWidth);
    const step = cards.length === 1
        ? 0
        : Math.max(20, Math.min(cardWidth + 8, totalSpread / (cards.length - 1)));

    cards.forEach((cardEl, index) => {
        cardEl.style.left = `${Math.round(index * step)}px`;
        cardEl.style.zIndex = String(index + 1);
    });

    handEl.style.minHeight = '230px';
}

export function renderSnapshot({ snapshot, ui, app, config, onChooseOption }) {
    app.snapshot = snapshot;

    const human = String(config.player_id);
    const ai = String(config.ai_player_id);
    const current = String(snapshot.current_player_id);
    const vp = snapshot.victory_points;
    const mana = snapshot.mana_pool;
    const manaCap = snapshot.mana_cap || {};
    const deckSizes = snapshot.deck_sizes || {};
    const legal = humanLegalActions(snapshot, config.player_id);

    app.playableCardSet = new Set(
        legal
            .filter((a) => a.kind === 'play_card' && a.card_id != null)
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
                app.legalMoveChoiceSet.add(`${cardId}|${loc}`);
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
    fillSelectFromOptions(
        ui.checkpointPath,
        snapshot.available_checkpoints || [],
        ui.checkpointPath.value || 'stats/checkpoints/ai_nn_distributed_latest.pt'
    );

    ui.hud.innerHTML = '';

    ui.vpSidebar.innerHTML = [
        `<div class="vp-row"><div class="vp-side-label">You</div>${renderVpTrack(vp[human] ?? 0)}</div>`,
        `<div class="vp-row"><div class="vp-side-label">Opp</div>${renderVpTrack(vp[ai] ?? 0)}</div>`,
    ].join('');

    ui.oppMana.innerHTML = renderManaTrack(manaCap[ai] ?? 0, mana[ai] ?? 0);
    ui.yourMana.innerHTML = renderManaTrack(manaCap[human] ?? 0, mana[human] ?? 0);
    ui.oppHand.innerHTML = renderOpponentHand(snapshot.opponent_hand_size);

    const underworld = snapshot.underworld || {};
    ui.oppUnderworld.innerHTML = renderUnderworldStack(underworld[ai] || []);
    ui.yourUnderworld.innerHTML = renderUnderworldStack(underworld[human] || []);

    ui.oppDeckCount.textContent = String(deckSizes[ai] ?? 0);
    ui.yourDeckCount.textContent = String(deckSizes[human] ?? 0);

    if (snapshot.pending_choice && !isOpeningMulligan) {
        const p = snapshot.pending_choice;
        const canChoose = Number(p.player_id) === config.player_id && p.choice_kind !== 'opening_mulligan';
        const cardNameById = buildCardNameMap(snapshot);
        const listedOptions = (p.options || []).map((opt) => ({
            value: opt,
            label: describeChoiceOption(opt, cardNameById),
        }));
        const optionChips = canChoose
            ? `<div class="pending-options">${listedOptions.map((opt) => `<span class="option-chip" data-choice-option="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</span>`).join('')}</div>`
            : '';
        const chooserLabel = Number(p.player_id) === config.player_id ? 'You' : 'Opponent';
        ui.pending.innerHTML = `
            <div class="pending">
                <strong>Pending choice for ${chooserLabel}</strong><br>
                ${escapeHtml(p.prompt || p.choice_kind)}<br>
                <span class="tiny">Options: ${listedOptions.map((opt) => escapeHtml(opt.label)).join(', ')}</span>
                ${optionChips}
            </div>
        `;

        if (canChoose) {
            ui.pending.querySelectorAll('[data-choice-option]').forEach((el) => {
                el.addEventListener('click', () => {
                    const optionId = el.getAttribute('data-choice-option');
                    if (!optionId) return;
                    onChooseOption(optionId);
                });
            });

            ui.choiceTitle.textContent = `Choice for ${chooserLabel}`;
            ui.choicePrompt.textContent = p.prompt || p.choice_kind;
            ui.choiceSub.textContent = 'Pick one option.';
            ui.choiceOptions.innerHTML = listedOptions
                .map((opt) => `<button class="choice-option-btn" data-choice-option="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</button>`)
                .join('');
            ui.choiceOptions.querySelectorAll('[data-choice-option]').forEach((el) => {
                el.addEventListener('click', () => {
                    const optionId = el.getAttribute('data-choice-option');
                    if (!optionId) return;
                    onChooseOption(optionId);
                });
            });
            ui.choiceModal.classList.add('open');
            ui.choiceModal.setAttribute('aria-hidden', 'false');
        } else {
            ui.choiceModal.classList.remove('open');
            ui.choiceModal.setAttribute('aria-hidden', 'true');
            ui.choiceOptions.innerHTML = '';
        }
    } else {
        ui.pending.innerHTML = '';
        ui.choiceModal.classList.remove('open');
        ui.choiceModal.setAttribute('aria-hidden', 'true');
        ui.choiceOptions.innerHTML = '';
    }

    const playerIds = Object.keys(snapshot.victory_points || { '1': 0, '2': 0 });
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
                    <span class="lane-score ${laneLeadClass}"><span class="you">${yourPower}</span> / <span class="opp">${oppPower}</span></span>
                </div>
                <div class="lane-row">
                    <div class="stack-cards">${renderCards(oppCards)}</div>
                </div>
                <div class="lane-row lane-drop" data-location-id="${loc.location_id}">
                    <div class="stack-cards">${renderCards(yourCards, { movableCards: app.movableChoiceCardSet })}</div>
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
            return `
                <div class="hand-card ${isOpeningMulligan ? 'mulligan-mode' : ''} ${app.mulliganSelected.has(c.id) ? 'marked' : ''} ${isPlayable ? 'playable' : ''} ${isUnplayable ? 'unplayable' : ''}" data-card-id="${c.id}" title="${isOpeningMulligan ? 'Click to toggle mulligan' : (isPlayable ? 'Playable now: drag to a location' : 'Not playable right now')}">
                    <div class="hand-card-headline">
                        <span class="stat-badge cost">${c.cost ?? '?'}</span>
                        <div class="hand-title-main" style="--title-scale: ${titleScale};" title="${escapeHtml(c.name)}"><span class="hand-title-text">${escapeHtml(handTitle)}</span></div>
                        <span class="stat-badge power">${c.power !== null ? c.power : '?'}</span>
                    </div>
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
        : '<div class="tiny">No cards in hand</div>';

    if (isOpeningMulligan) {
        ui.mulliganPanel.classList.remove('hidden');
        ui.mulliganInfo.textContent = canActMulligan
            ? `Selected: ${app.mulliganSelected.size}. Click cards with red X, then press Confirm mulligan.`
            : 'Waiting for the other player to finish mulligan.';
    } else {
        app.mulliganSelected.clear();
        ui.mulliganPanel.classList.add('hidden');
    }

    const isGameOver = snapshot.phase === 'GAME_OVER';
    ui.btnEndTurn.disabled = isGameOver ? false : !(canActMulligan || legal.some((a) => a.kind === 'end_turn'));
    ui.btnEndTurn.textContent = isGameOver ? 'New Game' : (isOpeningMulligan ? 'Confirm mulligan' : 'End Turn');

    if (isOpeningMulligan) {
        ui.status.textContent = '';
    } else if (isGameOver) {
        ui.status.textContent = gameOverStatusText(snapshot, human);
    } else {
        ui.status.textContent = current === human
            ? 'Your Turn | Drag cards from your hand to a lane to play'
            : 'Opponent Turn | Waiting for opponent';
    }

    renderActionHistory(snapshot, ui, config);
}
